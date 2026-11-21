"""Dialog for editing WhatsApp contacts."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from coco.tools.whatsapp import normalise_phone


class ContactsDialog(QDialog):
    def __init__(self, store, country: str = "91") -> None:
        super().__init__(None)
        self.store = store
        self.country = country
        self.setWindowTitle("Coco: WhatsApp contacts")
        self.resize(420, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Who Coco can message for you. Nicknames let you say \"message Mom\" or "
            "\"Mummy ko bolo...\". Saved only on this PC."
        ))  # fmt: skip
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        add = QPushButton("Add...", self)
        add.clicked.connect(self._add)
        delete = QPushButton("Delete", self)
        delete.clicked.connect(self._delete)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        row.addWidget(add)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for c in self.store.contacts():
            nick = f"  ({', '.join(c.aliases)})" if c.aliases else ""
            item = QListWidgetItem(f"{c.name}{nick}   +{c.phone}")
            item.setData(256, c.id)
            self.list.addItem(item)

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add contact", "Name (e.g. Rahul Sharma):")
        if not ok or not name.strip():
            return
        nicks, ok = QInputDialog.getText(
            self, "Add contact", "Nicknames, comma separated (optional, e.g. Mom, Mummy):"
        )
        if not ok:
            return
        raw, ok = QInputDialog.getText(
            self,
            "Add contact",
            f"WhatsApp number (with +country code; 10 digits = +{self.country}):",
        )
        if not ok:
            return
        phone = normalise_phone(raw, self.country)
        if phone is None:
            QMessageBox.warning(self, "Add contact", "That doesn't look like a phone number.")
            return
        self.store.add_contact(name, phone, [n for n in nicks.split(",") if n.strip()])
        self._reload()

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "Delete contact", f"Delete {item.text()}?") == (
            QMessageBox.StandardButton.Yes
        ):
            self.store.delete_contact(int(item.data(256)))
            self._reload()
