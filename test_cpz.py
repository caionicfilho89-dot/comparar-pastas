"""Testes do Comparar Pastas - só em pastas temporárias."""
import hashlib
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cpz_core as c  # noqa: E402


def w(root, rel, data, mtime=None):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def status(rows):
    return {r.rel: r.status for r in rows}


def test_all():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "A"), os.path.join(tmp, "B")
        os.makedirs(a)
        os.makedirs(b)
        t = 1_700_000_000
        w(a, "same.txt", b"hello", t)
        w(b, "same.txt", b"hello", t)
        w(a, "sub/diff.txt", b"aaaa", t)
        w(b, "sub/diff.txt", b"bbbb", t)            # mesmo tamanho, mesma data, conteúdo diferente
        w(a, "size.txt", b"1", t)
        w(b, "size.txt", b"12", t)
        w(a, "onlya.txt", b"A only", t)
        w(b, "onlyb.txt", b"B only", t)
        w(a, "x.tmp", b"ignore", t)
        w(a, ".git/config", b"ignore", t)
        w(a, "Thumbs.db", b"ignore", t)
        w(a, "mtime.txt", b"same", t)
        w(b, "mtime.txt", b"same", t + 500)

        ign = c.parse_ignore("*.tmp, Thumbs.db; .git/")
        assert ign == ["*.tmp", "Thumbs.db", ".git/"]
        assert c.is_ignored(".git/config", ign) and c.is_ignored("a/b/x.tmp", ign)
        assert not c.is_ignored("gitx/config", ign)

        fast = status(c.compare_folders(a, b, c.MODE_FAST, ign))
        assert fast == {"same.txt": c.SAME, "sub/diff.txt": c.SAME, "size.txt": c.DIFF,
                        "onlya.txt": c.ONLY_A, "onlyb.txt": c.ONLY_B, "mtime.txt": c.DIFF}, fast
        rows = c.compare_folders(a, b, c.MODE_EXACT, ign)
        ex = status(rows)
        assert ex["sub/diff.txt"] == c.DIFF and ex["same.txt"] == c.SAME and ex["mtime.txt"] == c.SAME
        s = c.summarize(rows)
        assert s[c.ONLY_A][0] == 1 and s[c.DIFF][0] == 2 and s[c.SAME][0] == 2

        # cancelamento
        try:
            c.compare_folders(a, b, c.MODE_FAST, ign, cancel=lambda: True)
            assert False
        except c.Cancelled:
            pass

        # plano A->B sem lixeira: nunca contém trash
        plan = c.build_plan(rows, a, b, "AB")
        acts = sorted((p.action, p.rel_dst) for p in plan)
        assert acts == [("copy_new", "onlya.txt"), ("copy_overwrite", "size.txt"),
                        ("copy_overwrite", "sub/diff.txt")], acts
        assert not any(p.action == "trash" for p in plan)
        # sem sobrescrever
        plan_n = c.build_plan(rows, a, b, "AB", include_diff=False)
        assert [p.action for p in plan_n] == ["copy_new"]
        # espelhar com lixeira
        pm = c.build_plan(rows, a, b, "AB", mirror_trash=True)
        assert [p.rel_dst for p in pm if p.action == "trash"] == ["onlyb.txt"]
        # seleção
        ps = c.build_plan(rows, a, b, "AB", selected={"onlya.txt"})
        assert len(ps) == 1
        # B->A
        pb = c.build_plan(rows, a, b, "BA")
        assert ("copy_new", "onlyb.txt") in [(p.action, p.rel_dst) for p in pb]
        assert not any(p.action == "trash" for p in c.build_plan(rows, a, b, "BA", mirror_trash=True))

        # execução com lixeira simulada (não toca a Lixeira real)
        trashed = []

        def fake_trash(path):
            trashed.append(path)
            os.rename(path, path + ".trashed_by_test")

        # B->A primeiro (cópia nova) e verificação
        rep = c.execute_plan(pb, c.MODE_EXACT)
        assert rep.fail_count == 0, c.report_text(rep)
        assert open(os.path.join(a, "onlyb.txt"), "rb").read() == b"B only"
        assert open(os.path.join(a, "sub", "diff.txt"), "rb").read() == b"bbbb"
        # sem restos .part
        for _, _, fs in os.walk(tmp):
            assert not [f for f in fs if f.endswith(".part")]

        # reexecutar plano velho: destino mudou -> não sobrescreve
        w(a, "size.txt", b"differs from B")
        rows2 = c.compare_folders(a, b, c.MODE_EXACT, ign)
        planx = c.build_plan(rows2, a, b, "BA")
        assert [p.action for p in planx] == ["copy_overwrite"]
        w(a, "size.txt", b"changed AGAIN, longer!!")
        repx = c.execute_plan(planx, c.MODE_EXACT)
        assert any(not r.ok and "mudou" in r.message for r in repx.results)

        # copy_new não sobrescreve arquivo que apareceu
        rows3 = c.compare_folders(a, b, c.MODE_FAST, ign)
        w(a, "late.txt", b"orig")
        p3 = c.build_plan(rows3, b, a, "AB")  # nada novo; monta manual
        item = c.PlanItem("copy_new", b, a, "same.txt", "late.txt", 5)
        r = c.execute_plan([item])
        assert not r.results[0].ok and open(os.path.join(a, "late.txt"), "rb").read() == b"orig"
        # caminho fora da pasta é recusado
        evil = c.PlanItem("copy_new", a, b, "same.txt", "../escape.txt", 5)
        assert not c.execute_plan([evil]).results[0].ok
        assert not os.path.exists(os.path.join(tmp, "escape.txt"))

        # espelhar A->B com lixeira
        rows4 = c.compare_folders(a, b, c.MODE_EXACT, ign)
        plan4 = c.build_plan(rows4, a, b, "AB", mirror_trash=True)
        rep4 = c.execute_plan(plan4, c.MODE_EXACT, trash_fn=fake_trash)
        assert rep4.fail_count == 0, c.report_text(rep4)
        # tudo que era só em B... (onlyb já foi copiado pra A, então virou igual)
        assert trashed == [] or all(t.startswith(b) for t in trashed)
        fin = status(c.compare_folders(a, b, c.MODE_EXACT, ign))
        assert c.ONLY_A not in fin.values() and c.DIFF not in fin.values()

        # lixeira: arquivo só em B é enviado; sem a flag nunca
        w(b, "extra.txt", b"extra")
        rows5 = c.compare_folders(a, b, c.MODE_FAST, ign)
        rep5 = c.execute_plan(c.build_plan(rows5, a, b, "AB"), trash_fn=fake_trash)
        assert os.path.exists(os.path.join(b, "extra.txt")) and trashed == []
        rows5 = c.compare_folders(a, b, c.MODE_FAST, ign)
        rep6 = c.execute_plan(c.build_plan(rows5, a, b, "AB", mirror_trash=True), trash_fn=fake_trash)
        assert rep6.fail_count == 0 and len(trashed) == 1, (c.report_text(rep6), trashed)
        assert not os.path.exists(os.path.join(b, "extra.txt"))
        assert os.path.exists(os.path.join(b, "extra.txt.trashed_by_test"))
        # lixeira recusa arquivo alterado
        w(b, "e2.txt", b"123")
        rows7 = c.compare_folders(a, b, c.MODE_FAST, ign)
        pl7 = c.build_plan(rows7, a, b, "AB", mirror_trash=True)
        w(b, "e2.txt", b"1234567")
        r7 = c.execute_plan(pl7, trash_fn=fake_trash)
        assert os.path.exists(os.path.join(b, "e2.txt")) and not r7.results[0].ok

        # exportação
        csvp, htmlp = os.path.join(tmp, "r.csv"), os.path.join(tmp, "r.html")
        c.export_csv(rows, csvp)
        c.export_html(rows, htmlp, a, b)
        assert "onlya.txt" in open(csvp, encoding="utf-8-sig").read()
        assert "<table>" in open(htmlp, encoding="utf-8").read()
        assert c.human_size(1536) == "1.5 KB"

        gui_smoke(tmp)


def gui_smoke(tmp):
    from PyQt6.QtWidgets import QApplication
    import importlib.machinery
    import importlib.util
    path = os.path.join(HERE, "comparar_pastas.pyw")
    loader = importlib.machinery.SourceFileLoader("cpz_gui", path)
    spec = importlib.util.spec_from_loader("cpz_gui", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    app = QApplication.instance() or QApplication([])
    a, b = os.path.join(tmp, "GA"), os.path.join(tmp, "GB")
    w(a, "n.txt", b"1")
    w(a, "d/m.txt", b"22")
    w(b, "m2.txt", b"3")
    win = m.ComparaPastas()
    win.ed_a.setText(a)
    win.ed_b.setText(b)
    win.start_compare()
    t0 = time.time()
    while win.worker.isRunning() and time.time() - t0 < 10:
        app.processEvents()
    win.worker.wait()
    app.processEvents()
    assert len(win.rows) == 3 and win.table.rowCount() == 3, (len(win.rows), win.table.rowCount())
    assert not win.chk_trash.isChecked()
    win.filters[c.ONLY_B].setChecked(False)
    assert win.table.rowCount() == 2
    win.search.setText("n.tx")
    assert win.table.rowCount() == 1
    win.export("csv") if False else None


if __name__ == "__main__":
    test_all()
    print("OK")
