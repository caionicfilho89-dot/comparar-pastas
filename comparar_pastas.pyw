"""Comparar Pastas - compara duas pastas (só em A, só em B, idêntico, diferente) e sincroniza
com segurança: prévia + confirmação, cópia atômica, verificação depois; o que sai de B vai pra
Lixeira (nunca apaga de vez). Tudo offline.
"""
import os
import sys
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cpz_core as core  # noqa: E402

ACCENT = "#7c5cff"
QSS = """
QWidget { background:#11131a; color:#e7e9f0; font:10pt 'Segoe UI'; }
#title { font:700 18pt 'Segoe UI'; }
#info { color:#8a93a8; }
#big { font:700 11pt 'Consolas','Segoe UI'; color:#79e0a8; }
QLineEdit, QComboBox { background:#1b202b; border:1px solid #2d3550; border-radius:8px; padding:6px; }
QPlainTextEdit { background:#0c0e14; border:1px solid #2d3550; border-radius:10px; color:#79e0a8;
    font:9pt 'Consolas'; }
QTableWidget { background:#0c0e14; border:1px solid #2d3550; border-radius:10px; gridline-color:#1b202b; }
QHeaderView::section { background:#1b202b; color:#8a93a8; border:0; padding:5px; }
QPushButton { background:#222a38; border:0; border-radius:10px; padding:9px 14px; }
QPushButton:hover { background:#2d3550; }
QPushButton:disabled { color:#5a6278; }
#go { background:#7c5cff; color:#fff; font-weight:600; padding:12px 16px; font-size:12pt; }
#go:hover { background:#8f74ff; }
#stop { background:#ff4d4d; color:#fff; font-weight:600; }
QProgressBar { background:#1b202b; border:0; border-radius:6px; height:10px; text-align:center; }
QProgressBar::chunk { background:#7c5cff; border-radius:6px; }
"""
ROW_COLORS = {core.ONLY_A: "#16324f", core.ONLY_B: "#4a3512", core.SAME: "#12301f",
              core.DIFF: "#4a1c1c"}


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(QColor("transparent"))
    p = QPainter(pm)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(4, 14, 26, 36, 5, 5)
    p.setBrush(QColor("#79e0a8"))
    p.drawRoundedRect(34, 14, 26, 36, 5, 5)
    p.end()
    return QIcon(pm)


class CompareThread(QThread):
    msg = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, a, b, mode, ignore):
        super().__init__()
        self.a, self.b, self.mode, self.ignore = a, b, mode, ignore
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            rows = core.compare_folders(self.a, self.b, self.mode, self.ignore,
                                        self.msg.emit, lambda: self._cancel)
            self.done.emit(rows)
        except core.Cancelled:
            self.failed.emit("Cancelado.")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"Erro: {e}")


class ExecThread(QThread):
    prog = pyqtSignal(int, int, str)
    done = pyqtSignal(object)

    def __init__(self, plan, mode):
        super().__init__()
        self.plan, self.mode = plan, mode
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        rep = core.execute_plan(self.plan, self.mode, self.prog.emit, lambda: self._cancel)
        self.done.emit(rep)


class PreviewDialog(QDialog):
    def __init__(self, parent, title, lines, summary, warn=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(summary))
        if warn:
            w = QLabel(warn)
            w.setStyleSheet("color:#ffb454; font-weight:600;")
            w.setWordWrap(True)
            lay.addWidget(w)
        box = QPlainTextEdit("\n".join(lines))
        box.setReadOnly(True)
        lay.addWidget(box, 1)
        r = QHBoxLayout()
        r.addStretch(1)
        no = QPushButton("Cancelar")
        no.clicked.connect(self.reject)
        yes = QPushButton("Confirmar e executar")
        yes.setObjectName("go")
        yes.clicked.connect(self.accept)
        r.addWidget(no)
        r.addWidget(yes)
        lay.addLayout(r)


class ComparaPastas(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comparar Pastas")
        self.setWindowIcon(app_icon())
        self.resize(1100, 740)
        self.rows = []
        self.worker = None
        self.root_a = self.root_b = ""
        self.last_mode = core.MODE_FAST

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)
        root.addWidget(QLabel("Comparar Pastas", objectName="title"))
        root.addWidget(QLabel("Vê o que só existe em A, só em B, o que é igual e o que mudou — e "
                              "sincroniza com prévia e confirmação. Offline.", objectName="info"))

        self.ed_a = QLineEdit()
        self.ed_b = QLineEdit()
        for lab, ed in (("Pasta A:", self.ed_a), ("Pasta B:", self.ed_b)):
            r = QHBoxLayout()
            l = QLabel(lab)
            l.setFixedWidth(60)
            r.addWidget(l)
            r.addWidget(ed, 1)
            b = QPushButton("Escolher…")
            b.clicked.connect(lambda _=False, e=ed: self._pick(e))
            r.addWidget(b)
            root.addLayout(r)

        o = QHBoxLayout()
        o.addWidget(QLabel("Modo:"))
        self.mode = QComboBox()
        self.mode.addItem("Rápido (tamanho + data)", core.MODE_FAST)
        self.mode.addItem("Exato (tamanho + SHA-256)", core.MODE_EXACT)
        o.addWidget(self.mode)
        o.addWidget(QLabel("Ignorar:"))
        self.ign = QLineEdit(", ".join(core.DEFAULT_IGNORE))
        self.ign.setToolTip("Padrões separados por vírgula. Termine com / para pastas (ex.: .git/)")
        o.addWidget(self.ign, 1)
        self.btn_cmp = QPushButton("Comparar")
        self.btn_cmp.setObjectName("go")
        self.btn_cmp.clicked.connect(self.start_compare)
        o.addWidget(self.btn_cmp)
        self.btn_stop = QPushButton("Cancelar")
        self.btn_stop.setObjectName("stop")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        o.addWidget(self.btn_stop)
        root.addLayout(o)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        root.addWidget(self.bar)
        self.status = QLabel("Escolha as duas pastas e clique em Comparar.", objectName="info")
        root.addWidget(self.status)
        self.summary = QLabel("", objectName="big")
        root.addWidget(self.summary)

        f = QHBoxLayout()
        f.addWidget(QLabel("Mostrar:"))
        self.filters = {}
        for st in (core.ONLY_A, core.ONLY_B, core.DIFF, core.SAME):
            cb = QCheckBox(core.STATUS_LABEL[st])
            cb.setChecked(st != core.SAME)
            cb.toggled.connect(self._refill)
            self.filters[st] = cb
            f.addWidget(cb)
        f.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nome…")
        self.search.textChanged.connect(self._refill)
        f.addWidget(self.search)
        root.addLayout(f)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Caminho", "Situação", "Tam. A", "Tam. B", "Data A", "Data B"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        a = QHBoxLayout()
        self.btn_ab = QPushButton("Copiar A → B")
        self.btn_ba = QPushButton("Copiar B → A")
        self.btn_mir = QPushButton("Espelhar A → B")
        self.btn_ab.clicked.connect(lambda: self.do_action("AB"))
        self.btn_ba.clicked.connect(lambda: self.do_action("BA"))
        self.btn_mir.clicked.connect(lambda: self.do_action("MIR"))
        self.chk_over = QCheckBox("incluir os diferentes (sobrescrever)")
        self.chk_over.setChecked(True)
        self.chk_trash = QCheckBox("também mandar pra Lixeira o que só existe em B")
        self.chk_trash.setChecked(False)
        self.chk_trash.setToolTip("Só vale no Espelhar. Vai pra Lixeira do Windows, nunca apaga de vez.")
        for w in (self.btn_ab, self.btn_ba, self.btn_mir):
            a.addWidget(w)
        a.addStretch(1)
        root.addLayout(a)
        c = QHBoxLayout()
        c.addWidget(self.chk_over)
        c.addWidget(self.chk_trash)
        c.addStretch(1)
        bc = QPushButton("Exportar CSV")
        bh = QPushButton("Exportar HTML")
        bc.clicked.connect(lambda: self.export("csv"))
        bh.clicked.connect(lambda: self.export("html"))
        c.addWidget(bc)
        c.addWidget(bh)
        root.addLayout(c)
        self.act_buttons = (self.btn_ab, self.btn_ba, self.btn_mir)
        self._set_actions(False)

    # ------------------------------------------------------------ util
    def _pick(self, ed):
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta", ed.text() or os.path.expanduser("~"))
        if d:
            ed.setText(os.path.normpath(d))

    def _set_actions(self, on):
        for b in self.act_buttons:
            b.setEnabled(on)

    def _busy(self, on):
        self.btn_cmp.setEnabled(not on)
        self.btn_stop.setEnabled(on)
        self.bar.setVisible(on)
        if on:
            self.bar.setRange(0, 0)
            self._set_actions(False)

    def _stop(self):
        if self.worker:
            self.worker.cancel()

    # ------------------------------------------------------------ comparar
    def start_compare(self):
        a, b = self.ed_a.text().strip(), self.ed_b.text().strip()
        if not (os.path.isdir(a) and os.path.isdir(b)):
            QMessageBox.warning(self, "Comparar Pastas", "Escolha duas pastas que existam.")
            return
        if os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)):
            QMessageBox.warning(self, "Comparar Pastas", "A e B são a mesma pasta.")
            return
        self.root_a, self.root_b = a, b
        self.last_mode = self.mode.currentData()
        self._busy(True)
        self.worker = CompareThread(a, b, self.last_mode, core.parse_ignore(self.ign.text()))
        self.worker.msg.connect(self.status.setText)
        self.worker.done.connect(self._compared)
        self.worker.failed.connect(self._compare_failed)
        self.worker.start()

    def _compare_failed(self, m):
        self._busy(False)
        self.status.setText(m)

    def _compared(self, rows):
        self.rows = rows
        self._busy(False)
        s = core.summarize(rows)
        self.status.setText(f"Pronto: {len(rows)} arquivos comparados.")
        self.summary.setText("   ".join(f"{core.STATUS_LABEL[k]}: {s[k][0]} ({core.human_size(s[k][1])})"
                                        for k in (core.ONLY_A, core.ONLY_B, core.DIFF, core.SAME)))
        self._set_actions(True)
        self._refill()

    def visible_rows(self):
        q = self.search.text().strip().lower()
        return [r for r in self.rows if self.filters[r.status].isChecked() and (not q or q in r.rel.lower())]

    def _refill(self, *_):
        rows = self.visible_rows()
        self.table.setRowCount(len(rows))

        def t(x):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(x)) if x else ""
        for i, r in enumerate(rows):
            vals = [r.rel, core.STATUS_LABEL[r.status],
                    core.human_size(r.size_a) if r.size_a >= 0 else "",
                    core.human_size(r.size_b) if r.size_b >= 0 else "", t(r.mtime_a), t(r.mtime_b)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setBackground(QColor(ROW_COLORS[r.status]))
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, r.key)
                self.table.setItem(i, c, it)

    def selected_keys(self):
        ks = set()
        for idx in self.table.selectionModel().selectedRows():
            it = self.table.item(idx.row(), 0)
            if it:
                ks.add(it.data(Qt.ItemDataRole.UserRole))
        return ks or None

    # ------------------------------------------------------------ ações
    def do_action(self, kind):
        if not self.rows:
            return
        sel = self.selected_keys()
        if sel is None:  # sem seleção: só o que está visível pelos filtros
            sel = {r.key for r in self.visible_rows()}
        direction = "BA" if kind == "BA" else "AB"
        trash = kind == "MIR" and self.chk_trash.isChecked()
        plan = core.build_plan(self.rows, self.root_a, self.root_b, direction, sel,
                               include_new=True, include_diff=self.chk_over.isChecked(),
                               mirror_trash=trash)
        if not plan:
            QMessageBox.information(self, "Comparar Pastas", "Nada a fazer com essa seleção/filtros.")
            return
        ps = core.plan_summary(plan)
        txt = (f"{ps['novos'][0]} novos ({core.human_size(ps['novos'][1])}), "
               f"{ps['sobrescrever'][0]} a sobrescrever ({core.human_size(ps['sobrescrever'][1])}), "
               f"{ps['lixeira'][0]} pra Lixeira ({core.human_size(ps['lixeira'][1])})")
        warn = ""
        if ps["sobrescrever"][0]:
            warn += "Atenção: arquivos diferentes no destino serão substituídos. "
        if ps["lixeira"][0]:
            warn += "Os arquivos só de B vão pra Lixeira do Windows (dá pra restaurar)."
        if PreviewDialog(self, "Prévia - confirme", core.describe_plan(plan), txt, warn).exec() \
                != QDialog.DialogCode.Accepted:
            return
        self._busy(True)
        self.bar.setRange(0, len(plan))
        self.worker = ExecThread(plan, self.last_mode)
        self.worker.prog.connect(self._exec_prog)
        self.worker.done.connect(self._exec_done)
        self.worker.start()

    def _exec_prog(self, i, n, name):
        self.bar.setRange(0, max(n, 1))
        self.bar.setValue(i)
        self.status.setText(f"Executando {i}/{n}: {name}")

    def _exec_done(self, rep):
        self._busy(False)
        self.status.setText(f"Terminado: {rep.ok_count} ok, {rep.fail_count} com problema. Recomparando…")
        QMessageBox.information(self, "Resultado (já verificado)", core.report_text(rep))
        self.start_compare()

    # ------------------------------------------------------------ exportar
    def export(self, kind):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salvar relatório", f"comparacao.{kind}",
                                              "CSV (*.csv)" if kind == "csv" else "HTML (*.html)")
        if not path:
            return
        if kind == "csv":
            core.export_csv(self.rows, path)
        else:
            core.export_html(self.rows, path, self.root_a, self.root_b)
        self.status.setText(f"Relatório salvo em {path}")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    w = ComparaPastas()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
