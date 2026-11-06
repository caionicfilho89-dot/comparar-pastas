"""Comparar Pastas - lógica: varredura, comparação, plano, execução com verificação, relatórios.

Nada aqui apaga arquivo de forma permanente: o que sai de B no "espelhar" vai pra Lixeira
(send2trash) e só se o plano foi montado com mirror_trash=True.
"""
import csv
import fnmatch
import hashlib
import html
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field

ONLY_A, ONLY_B, SAME, DIFF = "so_em_A", "so_em_B", "igual", "diferente"
STATUS_LABEL = {ONLY_A: "Só em A", ONLY_B: "Só em B", SAME: "Idêntico", DIFF: "Diferente"}
MODE_FAST, MODE_EXACT = "rapido", "exato"
MTIME_TOL = 2.0  # segundos (FAT/exFAT arredondam)
DEFAULT_IGNORE = ["*.tmp", "Thumbs.db", ".git/"]


class Cancelled(Exception):
    pass


@dataclass
class Entry:
    rel: str
    size: int
    mtime: float


@dataclass
class Row:
    key: str
    status: str
    rel_a: str = ""
    rel_b: str = ""
    size_a: int = -1
    size_b: int = -1
    mtime_a: float = 0.0
    mtime_b: float = 0.0

    @property
    def rel(self):
        return self.rel_a or self.rel_b


@dataclass
class PlanItem:
    action: str          # "copy_new" | "copy_overwrite" | "trash"
    src_root: str
    dst_root: str
    rel_src: str         # caminho relativo na origem ("" para trash)
    rel_dst: str         # caminho relativo no destino
    size: int
    dst_size: int = -1   # tamanho esperado do destino atual (overwrite/trash)
    dst_mtime: float = 0.0


@dataclass
class Result:
    item: PlanItem
    ok: bool
    message: str = ""


@dataclass
class Report:
    results: list = field(default_factory=list)

    @property
    def ok_count(self):
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self):
        return sum(1 for r in self.results if not r.ok)


# ---------------------------------------------------------------- ignorar
def parse_ignore(text):
    """Aceita string (vírgula/linha/;) ou lista; devolve lista de padrões limpos."""
    if isinstance(text, str):
        for sep in (";", ","):
            text = text.replace(sep, "\n")
        text = text.splitlines()
    return [p.strip() for p in text if p and p.strip()]


def is_ignored(rel, patterns, is_dir=False):
    """rel com separador qualquer. 'x/' casa pasta chamada x em qualquer nível;
    outros padrões casam nome do arquivo ou caminho relativo inteiro."""
    parts = rel.replace("\\", "/").split("/")
    full = "/".join(parts)
    for pat in patterns:
        p = pat.replace("\\", "/")
        if p.endswith("/"):
            name = p[:-1]
            dirs = parts if is_dir else parts[:-1]
            if any(fnmatch.fnmatch(d.lower(), name.lower()) for d in dirs):
                return True
        else:
            if fnmatch.fnmatch(parts[-1].lower(), p.lower()) or fnmatch.fnmatch(full.lower(), p.lower()):
                return True
    return False


# ---------------------------------------------------------------- varredura
def scan_folder(root, ignore=(), progress=None, cancel=None):
    """Varre recursivamente. Devolve {chave_normalizada: Entry}. Não segue links de pasta."""
    ignore = list(ignore)
    out = {}
    stack = [("", root)]
    n = 0
    while stack:
        rel_dir, path = stack.pop()
        if cancel and cancel():
            raise Cancelled()
        try:
            it = list(os.scandir(path))
        except OSError:
            continue
        for e in it:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if not is_ignored(rel, ignore, is_dir=True):
                        stack.append((rel, e.path))
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                if is_ignored(rel, ignore):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            out[os.path.normcase(rel)] = Entry(rel, st.st_size, st.st_mtime)
            n += 1
            if progress and n % 200 == 0:
                progress(f"Varrendo {os.path.basename(root) or root}: {n} arquivos")
    if progress:
        progress(f"{os.path.basename(root) or root}: {n} arquivos")
    return out


def file_hash(path, cancel=None, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel and cancel():
                raise Cancelled()
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- comparação
def compare_folders(root_a, root_b, mode=MODE_FAST, ignore=(), progress=None, cancel=None):
    """Devolve lista de Row ordenada por caminho."""
    sa = scan_folder(root_a, ignore, progress, cancel)
    sb = scan_folder(root_b, ignore, progress, cancel)
    keys = sorted(set(sa) | set(sb))
    rows = []
    total = len(keys)
    for i, k in enumerate(keys):
        if cancel and cancel():
            raise Cancelled()
        a, b = sa.get(k), sb.get(k)
        if a and not b:
            rows.append(Row(k, ONLY_A, a.rel, "", a.size, -1, a.mtime, 0.0))
        elif b and not a:
            rows.append(Row(k, ONLY_B, "", b.rel, -1, b.size, 0.0, b.mtime))
        else:
            if a.size != b.size:
                st = DIFF
            elif mode == MODE_EXACT:
                if progress:
                    progress(f"Conferindo ({i + 1}/{total}): {a.rel}")
                try:
                    same = file_hash(os.path.join(root_a, a.rel), cancel) == \
                        file_hash(os.path.join(root_b, b.rel), cancel)
                except OSError:
                    same = False
                st = SAME if same else DIFF
            else:
                st = SAME if abs(a.mtime - b.mtime) <= MTIME_TOL else DIFF
            rows.append(Row(k, st, a.rel, b.rel, a.size, b.size, a.mtime, b.mtime))
    return rows


def summarize(rows):
    """{status: (contagem, bytes)} - bytes pelo lado que existe (maior dos dois)."""
    s = {ONLY_A: [0, 0], ONLY_B: [0, 0], SAME: [0, 0], DIFF: [0, 0]}
    for r in rows:
        s[r.status][0] += 1
        s[r.status][1] += max(r.size_a, r.size_b, 0)
    return {k: tuple(v) for k, v in s.items()}


def human_size(n):
    n = float(max(n, 0))
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


# ---------------------------------------------------------------- plano
def build_plan(rows, root_a, root_b, direction="AB", selected=None, include_new=True,
               include_diff=True, mirror_trash=False):
    """Monta a lista de ações SEM executar nada.

    direction 'AB' copia A->B; 'BA' copia B->A.
    selected: conjunto de keys (None = todas as linhas elegíveis).
    Sobrescreve só linhas com status DIFF. mirror_trash (só direction AB) inclui
    mandar pra Lixeira o que só existe em B (status ONLY_B).
    """
    if direction not in ("AB", "BA"):
        raise ValueError("direction")
    src_root, dst_root = (root_a, root_b) if direction == "AB" else (root_b, root_a)
    plan = []
    for r in rows:
        if selected is not None and r.key not in selected:
            continue
        only_src = ONLY_A if direction == "AB" else ONLY_B
        rel_s = r.rel_a if direction == "AB" else r.rel_b
        rel_d = r.rel_b if direction == "AB" else r.rel_a
        ssz = r.size_a if direction == "AB" else r.size_b
        dsz = r.size_b if direction == "AB" else r.size_a
        dmt = r.mtime_b if direction == "AB" else r.mtime_a
        if r.status == only_src and include_new:
            plan.append(PlanItem("copy_new", src_root, dst_root, rel_s, rel_s, ssz))
        elif r.status == DIFF and include_diff:
            plan.append(PlanItem("copy_overwrite", src_root, dst_root, rel_s, rel_d, ssz, dsz, dmt))
        elif mirror_trash and direction == "AB" and r.status == ONLY_B:
            plan.append(PlanItem("trash", src_root, dst_root, "", r.rel_b, r.size_b, r.size_b, r.mtime_b))
    return plan


def plan_summary(plan):
    new = [p for p in plan if p.action == "copy_new"]
    over = [p for p in plan if p.action == "copy_overwrite"]
    tr = [p for p in plan if p.action == "trash"]
    return {"novos": (len(new), sum(p.size for p in new)),
            "sobrescrever": (len(over), sum(p.size for p in over)),
            "lixeira": (len(tr), sum(p.size for p in tr))}


def describe_plan(plan):
    lab = {"copy_new": "COPIAR (novo)", "copy_overwrite": "SOBRESCREVER",
           "trash": "LIXEIRA (só em B)"}
    return [f"{lab[p.action]}: {p.rel_dst}  ({human_size(p.size)})" for p in plan]


# ---------------------------------------------------------------- execução
def _inside(root, path):
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    try:
        return os.path.commonpath([r, p]) == r and p != r
    except ValueError:
        return False


def _copy_atomic(src, dst):
    d = os.path.dirname(dst)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(dst)}.cpz_{uuid.uuid4().hex[:8]}.part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)  # só o nosso temporário .part
            except OSError:
                pass


def _verify(src, dst, mode, cancel=None):
    if os.path.getsize(src) != os.path.getsize(dst):
        return False, "tamanho diferente após copiar"
    if mode == MODE_EXACT:
        if file_hash(src, cancel) != file_hash(dst, cancel):
            return False, "SHA-256 diferente após copiar"
    return True, "ok"


def _default_trash(path):
    from send2trash import send2trash
    send2trash(path)


def execute_plan(plan, mode=MODE_FAST, progress=None, cancel=None, trash_fn=None):
    """Executa o plano. Revalida cada item antes de mexer. Devolve Report."""
    trash_fn = trash_fn or _default_trash
    rep = Report()
    total = len(plan)
    for i, it in enumerate(plan):
        if cancel and cancel():
            rep.results.append(Result(it, False, "cancelado"))
            continue
        if progress:
            progress(i, total, it.rel_dst)
        try:
            rep.results.append(_run_item(it, mode, cancel, trash_fn))
        except Cancelled:
            rep.results.append(Result(it, False, "cancelado"))
        except Exception as e:  # noqa: BLE001 - um erro não derruba o resto
            rep.results.append(Result(it, False, f"erro: {e}"))
    if progress:
        progress(total, total, "")
    return rep


def _run_item(it, mode, cancel, trash_fn):
    dst = os.path.join(it.dst_root, it.rel_dst)
    if not _inside(it.dst_root, dst):
        return Result(it, False, "destino fora da pasta - ignorado")
    if it.action == "trash":
        if not os.path.isfile(dst) or os.path.islink(dst):
            return Result(it, False, "não é mais um arquivo - ignorado")
        if os.path.getsize(dst) != it.dst_size:
            return Result(it, False, "mudou desde a comparação - ignorado")
        trash_fn(dst)
        if os.path.exists(dst):
            return Result(it, False, "continua na pasta após enviar à Lixeira")
        return Result(it, True, "enviado à Lixeira")
    src = os.path.join(it.src_root, it.rel_src)
    if not _inside(it.src_root, src) or not os.path.isfile(src):
        return Result(it, False, "origem sumiu - ignorado")
    if it.action == "copy_new":
        if os.path.lexists(dst):
            return Result(it, False, "já existe no destino agora - não sobrescrevi")
    elif it.action == "copy_overwrite":
        if not os.path.isfile(dst):
            return Result(it, False, "destino sumiu - ignorado")
        st = os.stat(dst)
        if st.st_size != it.dst_size or abs(st.st_mtime - it.dst_mtime) > MTIME_TOL:
            return Result(it, False, "destino mudou desde a comparação - não sobrescrevi")
    else:
        return Result(it, False, "ação desconhecida")
    _copy_atomic(src, dst)
    ok, msg = _verify(src, dst, mode, cancel)
    return Result(it, ok, "copiado e verificado" if ok else msg)


def report_text(rep):
    lines = [f"{rep.ok_count} ok, {rep.fail_count} com problema"]
    for r in rep.results:
        if not r.ok:
            lines.append(f"  FALHOU {r.item.rel_dst}: {r.message}")
    return "\n".join(lines)


# ---------------------------------------------------------------- exportar
def _fmt_t(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def export_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Caminho", "Situação", "Tamanho A", "Tamanho B", "Data A", "Data B"])
        for r in rows:
            w.writerow([r.rel, STATUS_LABEL[r.status], r.size_a if r.size_a >= 0 else "",
                        r.size_b if r.size_b >= 0 else "", _fmt_t(r.mtime_a), _fmt_t(r.mtime_b)])


def export_html(rows, path, root_a="", root_b=""):
    colors = {ONLY_A: "#2b4a7a", ONLY_B: "#7a5a2b", SAME: "#1f4d36", DIFF: "#7a2b2b"}
    s = summarize(rows)
    out = ["<!doctype html><meta charset='utf-8'><title>Comparar Pastas</title>",
           "<style>body{font:14px Segoe UI;background:#11131a;color:#e7e9f0;padding:20px}"
           "table{border-collapse:collapse;width:100%}td,th{padding:5px 8px;border-bottom:1px solid #2d3550;"
           "text-align:left}</style>",
           "<h2>Comparar Pastas</h2>",
           f"<p>A: {html.escape(root_a)}<br>B: {html.escape(root_b)}</p><ul>"]
    for k in (ONLY_A, ONLY_B, SAME, DIFF):
        out.append(f"<li>{STATUS_LABEL[k]}: {s[k][0]} ({human_size(s[k][1])})</li>")
    out.append("</ul><table><tr><th>Caminho</th><th>Situação</th><th>Tam. A</th><th>Tam. B</th>"
               "<th>Data A</th><th>Data B</th></tr>")
    for r in rows:
        out.append(f"<tr style='background:{colors[r.status]}'><td>{html.escape(r.rel)}</td>"
                   f"<td>{STATUS_LABEL[r.status]}</td>"
                   f"<td>{human_size(r.size_a) if r.size_a >= 0 else ''}</td>"
                   f"<td>{human_size(r.size_b) if r.size_b >= 0 else ''}</td>"
                   f"<td>{_fmt_t(r.mtime_a)}</td><td>{_fmt_t(r.mtime_b)}</td></tr>")
    out.append("</table>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
