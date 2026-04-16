from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional

import qrcode
import filetype
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
QR_DIR = DATA_DIR / "qr"
DB_PATH = DATA_DIR / "assetmgmt.db"


def ensure_runtime_directories() -> None:
    for path in (DATA_DIR, UPLOAD_DIR, QR_DIR):
        path.mkdir(parents=True, exist_ok=True)


ensure_runtime_directories()

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "doc", "docx", "xls", "xlsx", "csv", "zip"
}
ALLOWED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
    "text/plain", "text/csv", "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
}
MAX_FILE_SIZE_MB = 25
PER_PAGE = 20
DEVICE_STATUSES = [
    "aktywne",
    "w magazynie",
    "w serwisie",
    "wycofane",
    "uszkodzone",
    "do utylizacji",
]
PART_STATUSES = ["na stanie"]
ROLES = ["admin", "edytor", "czytacz"]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["WTF_CSRF_TIME_LIMIT"] = None

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SoftDeleteMixin:
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None


class User(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="czytacz")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Config(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inventory_prefix = db.Column(db.String(32), default="AST", nullable=False)
    next_inventory_number = db.Column(db.Integer, default=1, nullable=False)
    qr_label_template = db.Column(db.String(255), default="{inventory_number} | {manufacturer} {model_name}", nullable=False)


class Building(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    rooms = db.relationship("Room", backref="building", lazy=True)


class Room(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.Integer, db.ForeignKey("building.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    __table_args__ = (db.UniqueConstraint("building_id", "name", name="uq_room_per_building"),)
    racks = db.relationship("Rack", backref="room", lazy=True)


class Rack(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    __table_args__ = (db.UniqueConstraint("room_id", "name", name="uq_rack_per_room"),)
    shelves = db.relationship("Shelf", backref="rack", lazy=True)


class Shelf(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rack_id = db.Column(db.Integer, db.ForeignKey("rack.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    __table_args__ = (db.UniqueConstraint("rack_id", "name", name="uq_shelf_per_rack"),)


class DeviceModel(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    manufacturer = db.Column(db.String(128), nullable=False)
    model_code = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    devices = db.relationship("Device", backref="device_model", lazy=True)
    __table_args__ = (db.UniqueConstraint("manufacturer", "model_code", name="uq_device_model"),)


class Device(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_model_id = db.Column(db.Integer, db.ForeignKey("device_model.id"), nullable=False)
    inventory_number = db.Column(db.String(64), unique=True, nullable=False)
    serial_number = db.Column(db.String(128), unique=True, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="aktywne")
    shelf_id = db.Column(db.Integer, db.ForeignKey("shelf.id"), nullable=True)
    qr_code_value = db.Column(db.String(255), unique=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)


class PartType(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    manufacturer = db.Column(db.String(128), nullable=False)
    model_code = db.Column(db.String(128), nullable=False)
    part_number = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    items = db.relationship("PartItem", backref="part_type", lazy=True)
    __table_args__ = (db.UniqueConstraint("manufacturer", "part_number", name="uq_part_type"),)


class PartItem(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_type_id = db.Column(db.Integer, db.ForeignKey("part_type.id"), nullable=False)
    serial_number = db.Column(db.String(128), unique=True, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="na stanie")
    shelf_id = db.Column(db.Integer, db.ForeignKey("shelf.id"), nullable=True)
    notes = db.Column(db.Text, nullable=True)


class PartCompatibility(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_type_id = db.Column(db.Integer, db.ForeignKey("part_type.id"), nullable=False)
    device_model_id = db.Column(db.Integer, db.ForeignKey("device_model.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("part_type_id", "device_model_id", name="uq_part_device_compatibility"),)


class Attachment(TimestampMixin, SoftDeleteMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(32), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), unique=True, nullable=False)
    mime_type = db.Column(db.String(128), nullable=True)
    file_size = db.Column(db.Integer, nullable=False)


Device.shelf = db.relationship("Shelf", backref="devices", lazy=True)
PartItem.shelf = db.relationship("Shelf", backref="part_items", lazy=True)


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "location_label": location_label,
        "request": request,
    }


def get_current_user() -> Optional[User]:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.filter_by(id=user_id, is_deleted=False, is_active=True).first()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect(url_for("login"))
            if user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def active_query(model):
    return model.query.filter_by(is_deleted=False)


def get_config() -> Config:
    cfg = Config.query.first()
    if not cfg:
        cfg = Config()
        db.session.add(cfg)
        db.session.commit()
    return cfg


def generate_inventory_number() -> str:
    cfg = get_config()
    value = f"{cfg.inventory_prefix}-{cfg.next_inventory_number:06d}"
    cfg.next_inventory_number += 1
    db.session.commit()
    return value


def ensure_unique_or_raise(value: Optional[str], model, field_name: str, label: str, current_id: Optional[int] = None):
    if not value:
        return
    q = model.query.filter(getattr(model, field_name) == value)
    if current_id is not None:
        q = q.filter(model.id != current_id)
    existing = q.first()
    if existing:
        raise ValueError(f"Pole '{label}' musi być unikalne.")


def location_label(shelf: Optional[Shelf]) -> str:
    if not shelf:
        return "—"
    return f"{shelf.rack.room.building.name} / {shelf.rack.room.name} / {shelf.rack.name} / {shelf.name}"


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def sniff_file_type(file_storage) -> tuple[str | None, str | None]:
    file_storage.stream.seek(0)
    head = file_storage.stream.read(261)
    file_storage.stream.seek(0)
    kind = filetype.guess(head)
    if kind:
        return kind.extension, kind.mime
    return None, None


def validate_uploaded_file(file_storage) -> None:
    if not allowed_file(file_storage.filename or ""):
        raise ValueError("Niedozwolony typ pliku.")
    ext = (file_storage.filename or "").rsplit('.', 1)[-1].lower() if '.' in (file_storage.filename or '') else ''
    detected_ext, detected_mime = sniff_file_type(file_storage)
    text_like_extensions = {"txt", "csv", "doc", "docx", "xls", "xlsx"}
    if ext in text_like_extensions and not detected_mime:
        return
    if detected_ext and detected_ext != ext and not ({detected_ext, ext} <= {"jpg", "jpeg"}):
        raise ValueError("Zawartość pliku nie zgadza się z rozszerzeniem.")
    if detected_mime and detected_mime not in ALLOWED_MIME_TYPES:
        raise ValueError("Niedozwolony typ zawartości pliku.")


def resolve_attachment_entity(entity_type: str, entity_id: int):
    allowed = {
        "device": Device,
        "part_item": PartItem,
    }
    model = allowed.get(entity_type)
    if not model:
        raise ValueError("Nieprawidłowy typ encji.")
    entity = active_query(model).filter_by(id=entity_id).first()
    if not entity:
        raise ValueError("Nie znaleziono wskazanego obiektu.")
    return entity


def make_qr(device: Device) -> str:
    filename = f"device_{device.id}.png"
    filepath = QR_DIR / filename
    img = qrcode.make(device_detail_url(device))
    img.save(filepath)
    return filename


def device_detail_url(device: Device) -> str:
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")
    return f"{base_url.rstrip('/')}{url_for('device_detail', device_id=device.id)}"


def get_page() -> int:
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    return max(page, 1)


def paginate(query):
    return query.paginate(page=get_page(), per_page=PER_PAGE, error_out=False)


def parse_csv(file_storage, expected_headers: list[str]) -> list[dict]:
    payload = file_storage.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(payload), delimiter=",")
    if (reader.fieldnames or []) != expected_headers:
        raise ValueError(f"Nieprawidłowy nagłówek CSV. Oczekiwano: {expected_headers}")
    return list(reader)


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Hasło musi mieć co najmniej 8 znaków.")


def upsert_location(building_name: str, room_name: str, rack_name: str, shelf_name: str) -> Shelf:
    building = Building.query.filter_by(name=building_name).first()
    if not building:
        building = Building(name=building_name)
        db.session.add(building)
        db.session.flush()
    room = Room.query.filter_by(building_id=building.id, name=room_name).first()
    if not room:
        room = Room(building_id=building.id, name=room_name)
        db.session.add(room)
        db.session.flush()
    rack = Rack.query.filter_by(room_id=room.id, name=rack_name).first()
    if not rack:
        rack = Rack(room_id=room.id, name=rack_name)
        db.session.add(rack)
        db.session.flush()
    shelf = Shelf.query.filter_by(rack_id=rack.id, name=shelf_name).first()
    if not shelf:
        shelf = Shelf(rack_id=rack.id, name=shelf_name)
        db.session.add(shelf)
        db.session.flush()
    return shelf


def find_shelf_by_path(building_name: str, room_name: str, rack_name: str, shelf_name: str) -> Optional[Shelf]:
    return (
        Shelf.query.join(Rack).join(Room).join(Building)
        .filter(
            Building.name == building_name,
            Room.name == room_name,
            Rack.name == rack_name,
            Shelf.name == shelf_name,
            Shelf.is_deleted == False,
            Rack.is_deleted == False,
            Room.is_deleted == False,
            Building.is_deleted == False,
        )
        .first()
    )


def initialize_database() -> None:
    with app.app_context():
        db.create_all()
        cfg = get_config()
        _ = cfg
        if not User.query.filter_by(username="admin").first():
            user = User(username="admin", role="admin")
            user.set_password(os.environ.get("ADMIN_PASSWORD", "admin12345"))
            db.session.add(user)
            db.session.commit()


initialize_database()


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"], error_message="Zbyt wiele prób logowania. Spróbuj ponownie za chwilę.")
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, is_deleted=False, is_active=True).first()
        if user and user.check_password(password):
            session.clear()
            session.permanent = True
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))
        flash("Nieprawidłowy login lub hasło.", "danger")
    return render_template("login.html", title="Logowanie")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        title="Dashboard",
        devices_count=active_query(Device).count(),
        parts_count=active_query(PartItem).count(),
        in_service_count=active_query(Device).filter_by(status="w serwisie").count(),
        broken_count=active_query(Device).filter_by(status="uszkodzone").count(),
        latest_devices=active_query(Device).order_by(Device.created_at.desc()).limit(5).all(),
        latest_parts=active_query(PartItem).order_by(PartItem.created_at.desc()).limit(5).all(),
    )


@app.route("/urzadzenia")
@login_required
def devices():
    status = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()
    query = active_query(Device).join(DeviceModel)
    if status:
        query = query.filter(Device.status == status)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Device.inventory_number.ilike(like),
                Device.serial_number.ilike(like),
                DeviceModel.name.ilike(like),
                DeviceModel.manufacturer.ilike(like),
                DeviceModel.model_code.ilike(like),
            )
        )
    page_obj = paginate(query.order_by(Device.created_at.desc()))
    return render_template("devices.html", title="Urządzenia", items=page_obj, statuses=DEVICE_STATUSES, status=status, q=search)


@app.route("/urzadzenia/nowe", methods=["GET", "POST"])
@role_required("admin", "edytor")
def device_create():
    models = active_query(DeviceModel).order_by(DeviceModel.manufacturer, DeviceModel.model_code).all()
    shelves = active_query(Shelf).all()
    if request.method == "POST":
        try:
            inventory_number = request.form.get("inventory_number", "").strip() or generate_inventory_number()
            qr_code_value = request.form.get("qr_code_value", "").strip() or str(uuid.uuid4())
            serial_number = request.form.get("serial_number", "").strip() or None
            ensure_unique_or_raise(inventory_number, Device, "inventory_number", "Nr inwentarzowy")
            ensure_unique_or_raise(qr_code_value, Device, "qr_code_value", "Kod QR")
            ensure_unique_or_raise(serial_number, Device, "serial_number", "Numer seryjny")
            device = Device(
                device_model_id=int(request.form["device_model_id"]),
                inventory_number=inventory_number,
                serial_number=serial_number,
                status=request.form.get("status", DEVICE_STATUSES[0]),
                shelf_id=int(request.form["shelf_id"]) if request.form.get("shelf_id") else None,
                qr_code_value=qr_code_value,
                notes=request.form.get("notes", "").strip() or None,
            )
            db.session.add(device)
            db.session.commit()
            make_qr(device)
            flash("Urządzenie zostało dodane.", "success")
            return redirect(url_for("device_detail", device_id=device.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("device_form.html", title="Nowe urządzenie", models=models, shelves=shelves, statuses=DEVICE_STATUSES, item=None)


@app.route("/urzadzenia/<int:device_id>")
@login_required
def device_detail(device_id: int):
    device = active_query(Device).filter_by(id=device_id).first_or_404()
    attachments = active_query(Attachment).filter_by(entity_type="device", entity_id=device.id).all()
    qr_filename = make_qr(device)
    return render_template("device_detail.html", title=device.inventory_number, device=device, attachments=attachments, qr_filename=qr_filename)


@app.route("/urzadzenia/<int:device_id>/edytuj", methods=["GET", "POST"])
@role_required("admin", "edytor")
def device_edit(device_id: int):
    device = active_query(Device).filter_by(id=device_id).first_or_404()
    models = active_query(DeviceModel).order_by(DeviceModel.manufacturer, DeviceModel.model_code).all()
    shelves = active_query(Shelf).all()
    if request.method == "POST":
        try:
            inventory_number = request.form.get("inventory_number", "").strip()
            qr_code_value = request.form.get("qr_code_value", "").strip()
            serial_number = request.form.get("serial_number", "").strip() or None
            ensure_unique_or_raise(inventory_number, Device, "inventory_number", "Nr inwentarzowy", device.id)
            ensure_unique_or_raise(qr_code_value, Device, "qr_code_value", "Kod QR", device.id)
            ensure_unique_or_raise(serial_number, Device, "serial_number", "Numer seryjny", device.id)
            device.device_model_id = int(request.form["device_model_id"])
            device.inventory_number = inventory_number
            device.serial_number = serial_number
            device.status = request.form.get("status", DEVICE_STATUSES[0])
            device.shelf_id = int(request.form["shelf_id"]) if request.form.get("shelf_id") else None
            device.qr_code_value = qr_code_value
            device.notes = request.form.get("notes", "").strip() or None
            db.session.commit()
            make_qr(device)
            flash("Urządzenie zostało zaktualizowane.", "success")
            return redirect(url_for("device_detail", device_id=device.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("device_form.html", title="Edytuj urządzenie", models=models, shelves=shelves, statuses=DEVICE_STATUSES, item=device)


@app.route("/urzadzenia/<int:device_id>/usun", methods=["POST"])
@role_required("admin")
def device_delete(device_id: int):
    device = active_query(Device).filter_by(id=device_id).first_or_404()
    device.soft_delete()
    db.session.commit()
    flash("Urządzenie zostało logicznie usunięte.", "warning")
    return redirect(url_for("devices"))


@app.route("/urzadzenia/<int:device_id>/przywroc", methods=["POST"])
@role_required("admin")
def device_restore(device_id: int):
    device = Device.query.filter_by(id=device_id, is_deleted=True).first_or_404()
    device.restore()
    db.session.commit()
    flash("Urządzenie zostało przywrócone.", "success")
    return redirect(url_for("deleted_records"))


@app.route("/urzadzenia/<int:device_id>/qr")
@login_required
def device_qr_print(device_id: int):
    device = active_query(Device).filter_by(id=device_id).first_or_404()
    qr_filename = make_qr(device)
    config = get_config()
    label_text = config.qr_label_template.format(
        inventory_number=device.inventory_number,
        manufacturer=device.device_model.manufacturer,
        model_name=device.device_model.model_code,
    )
    return render_template("device_qr.html", title="Druk QR", device=device, qr_filename=qr_filename, label_text=label_text, qr_url=device_detail_url(device))


@app.route("/czesci")
@login_required
def parts():
    search = request.args.get("q", "").strip()
    query = active_query(PartItem).join(PartType)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                PartItem.serial_number.ilike(like),
                PartType.name.ilike(like),
                PartType.manufacturer.ilike(like),
                PartType.model_code.ilike(like),
                PartType.part_number.ilike(like),
            )
        )
    page_obj = paginate(query.order_by(PartItem.created_at.desc()))
    return render_template("parts.html", title="Części", items=page_obj, q=search)


@app.route("/czesci/nowa", methods=["GET", "POST"])
@role_required("admin", "edytor")
def part_create():
    part_types = active_query(PartType).order_by(PartType.manufacturer, PartType.model_code).all()
    shelves = active_query(Shelf).all()
    if request.method == "POST":
        try:
            serial_number = request.form.get("serial_number", "").strip() or None
            ensure_unique_or_raise(serial_number, PartItem, "serial_number", "Numer seryjny")
            item = PartItem(
                part_type_id=int(request.form["part_type_id"]),
                serial_number=serial_number,
                status="na stanie",
                shelf_id=int(request.form["shelf_id"]) if request.form.get("shelf_id") else None,
                notes=request.form.get("notes", "").strip() or None,
            )
            db.session.add(item)
            db.session.commit()
            flash("Część została dodana.", "success")
            return redirect(url_for("part_detail", part_id=item.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("part_form.html", title="Nowa część", part_types=part_types, shelves=shelves, item=None)


@app.route("/czesci/<int:part_id>")
@login_required
def part_detail(part_id: int):
    item = active_query(PartItem).filter_by(id=part_id).first_or_404()
    attachments = active_query(Attachment).filter_by(entity_type="part_item", entity_id=item.id).all()
    return render_template("part_detail.html", title=item.part_type.name, item=item, attachments=attachments)


@app.route("/czesci/<int:part_id>/edytuj", methods=["GET", "POST"])
@role_required("admin", "edytor")
def part_edit(part_id: int):
    item = active_query(PartItem).filter_by(id=part_id).first_or_404()
    part_types = active_query(PartType).order_by(PartType.manufacturer, PartType.model_code).all()
    shelves = active_query(Shelf).all()
    if request.method == "POST":
        try:
            serial_number = request.form.get("serial_number", "").strip() or None
            ensure_unique_or_raise(serial_number, PartItem, "serial_number", "Numer seryjny", item.id)
            item.part_type_id = int(request.form["part_type_id"])
            item.serial_number = serial_number
            item.shelf_id = int(request.form["shelf_id"]) if request.form.get("shelf_id") else None
            item.notes = request.form.get("notes", "").strip() or None
            db.session.commit()
            flash("Część została zaktualizowana.", "success")
            return redirect(url_for("part_detail", part_id=item.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("part_form.html", title="Edytuj część", part_types=part_types, shelves=shelves, item=item)


@app.route("/czesci/<int:part_id>/usun", methods=["POST"])
@role_required("admin")
def part_delete(part_id: int):
    item = active_query(PartItem).filter_by(id=part_id).first_or_404()
    item.soft_delete()
    db.session.commit()
    flash("Część została logicznie usunięta.", "warning")
    return redirect(url_for("parts"))


@app.route("/czesci/<int:part_id>/przywroc", methods=["POST"])
@role_required("admin")
def part_restore(part_id: int):
    item = PartItem.query.filter_by(id=part_id, is_deleted=True).first_or_404()
    item.restore()
    db.session.commit()
    flash("Część została przywrócona.", "success")
    return redirect(url_for("deleted_records"))


@app.route("/modele-urzadzen")
@login_required
def device_models():
    page_obj = paginate(active_query(DeviceModel).order_by(DeviceModel.manufacturer, DeviceModel.model_code))
    return render_template("device_models.html", title="Modele urządzeń", items=page_obj)


@app.route("/modele-urzadzen/nowy", methods=["GET", "POST"])
@role_required("admin", "edytor")
def device_model_create():
    if request.method == "POST":
        item = DeviceModel(
            name=request.form.get("name", "").strip(),
            manufacturer=request.form.get("manufacturer", "").strip(),
            model_code=request.form.get("model_code", "").strip(),
            description=request.form.get("description", "").strip() or None,
            is_active=bool(request.form.get("is_active")),
        )
        db.session.add(item)
        db.session.commit()
        flash("Model urządzenia został dodany.", "success")
        return redirect(url_for("device_models"))
    return render_template("device_model_form.html", title="Nowy model urządzenia", item=None)


@app.route("/modele-urzadzen/<int:model_id>/edytuj", methods=["GET", "POST"])
@role_required("admin", "edytor")
def device_model_edit(model_id: int):
    item = active_query(DeviceModel).filter_by(id=model_id).first_or_404()
    if request.method == "POST":
        item.name = request.form.get("name", "").strip()
        item.manufacturer = request.form.get("manufacturer", "").strip()
        item.model_code = request.form.get("model_code", "").strip()
        item.description = request.form.get("description", "").strip() or None
        item.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Model urządzenia został zaktualizowany.", "success")
        return redirect(url_for("device_models"))
    return render_template("device_model_form.html", title="Edytuj model urządzenia", item=item)


@app.route("/typy-czesci")
@login_required
def part_types():
    page_obj = paginate(active_query(PartType).order_by(PartType.manufacturer, PartType.model_code))
    return render_template("part_types.html", title="Typy części", items=page_obj)


@app.route("/typy-czesci/nowy", methods=["GET", "POST"])
@role_required("admin", "edytor")
def part_type_create():
    device_model_items = active_query(DeviceModel).order_by(DeviceModel.manufacturer, DeviceModel.model_code).all()
    if request.method == "POST":
        part_type = PartType(
            name=request.form.get("name", "").strip(),
            manufacturer=request.form.get("manufacturer", "").strip(),
            model_code=request.form.get("model_code", "").strip(),
            part_number=request.form.get("part_number", "").strip(),
            description=request.form.get("description", "").strip() or None,
            is_active=bool(request.form.get("is_active")),
        )
        db.session.add(part_type)
        db.session.flush()
        for model_id in request.form.getlist("compatible_models"):
            db.session.add(PartCompatibility(part_type_id=part_type.id, device_model_id=int(model_id)))
        db.session.commit()
        flash("Typ części został dodany.", "success")
        return redirect(url_for("part_types"))
    return render_template("part_type_form.html", title="Nowy typ części", item=None, device_models=device_model_items, selected=[])


@app.route("/typy-czesci/<int:type_id>/edytuj", methods=["GET", "POST"])
@role_required("admin", "edytor")
def part_type_edit(type_id: int):
    item = active_query(PartType).filter_by(id=type_id).first_or_404()
    device_model_items = active_query(DeviceModel).order_by(DeviceModel.manufacturer, DeviceModel.model_code).all()
    selected = [x.device_model_id for x in PartCompatibility.query.filter_by(part_type_id=item.id).all()]
    if request.method == "POST":
        item.name = request.form.get("name", "").strip()
        item.manufacturer = request.form.get("manufacturer", "").strip()
        item.model_code = request.form.get("model_code", "").strip()
        item.part_number = request.form.get("part_number", "").strip()
        item.description = request.form.get("description", "").strip() or None
        item.is_active = bool(request.form.get("is_active"))
        PartCompatibility.query.filter_by(part_type_id=item.id).delete()
        for model_id in request.form.getlist("compatible_models"):
            db.session.add(PartCompatibility(part_type_id=item.id, device_model_id=int(model_id)))
        db.session.commit()
        flash("Typ części został zaktualizowany.", "success")
        return redirect(url_for("part_types"))
    return render_template("part_type_form.html", title="Edytuj typ części", item=item, device_models=device_model_items, selected=selected)


@app.route("/lokalizacje")
@login_required
def locations():
    buildings = active_query(Building).order_by(Building.name).all()
    return render_template("locations.html", title="Lokalizacje", buildings=buildings)


@app.route("/lokalizacje/nowa", methods=["GET", "POST"])
@role_required("admin")
def location_create():
    if request.method == "POST":
        try:
            upsert_location(
                request.form.get("building", "").strip(),
                request.form.get("room", "").strip(),
                request.form.get("rack", "").strip(),
                request.form.get("shelf", "").strip(),
            )
            db.session.commit()
            flash("Lokalizacja została dodana.", "success")
            return redirect(url_for("locations"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Nie udało się zapisać lokalizacji: {exc}", "danger")
    return render_template("location_form.html", title="Nowa lokalizacja")


@app.route("/administracja")
@role_required("admin")
def admin_panel():
    users = active_query(User).order_by(User.username).all()
    config = get_config()
    return render_template("admin.html", title="Administracja", users=users, config=config)


@app.route("/administracja/uzytkownicy/nowy", methods=["GET", "POST"])
@role_required("admin")
def user_create():
    if request.method == "POST":
        try:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "czytacz")
            if role not in ROLES:
                raise ValueError("Nieprawidłowa rola.")
            validate_password(password)
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Użytkownik został dodany.", "success")
            return redirect(url_for("admin_panel"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("user_form.html", title="Nowy użytkownik", roles=ROLES)


@app.route("/administracja/konfiguracja", methods=["POST"])
@role_required("admin")
def config_update():
    cfg = get_config()
    cfg.inventory_prefix = request.form.get("inventory_prefix", "AST").strip() or "AST"
    cfg.qr_label_template = request.form.get("qr_label_template", "{inventory_number} | {manufacturer} {model_name}").strip() or "{inventory_number} | {manufacturer} {model_name}"
    db.session.commit()
    flash("Konfiguracja została zapisana.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/administracja/usuniete")
@role_required("admin")
def deleted_records():
    return render_template(
        "deleted.html",
        title="Usunięte rekordy",
        deleted_devices=Device.query.filter_by(is_deleted=True).all(),
        deleted_parts=PartItem.query.filter_by(is_deleted=True).all(),
    )


@app.route("/administracja/import/<entity>", methods=["GET", "POST"])
@role_required("admin")
def import_csv(entity: str):
    expected = {
        "lokalizacje": ["budynek", "pomieszczenie", "regal", "polka"],
        "modele_urzadzen": ["nazwa", "producent", "model", "opis", "aktywny"],
        "typy_czesci": ["nazwa", "producent", "model", "part_number", "opis", "aktywny", "kompatybilne_modele"],
        "urzadzenia": ["producent", "model", "nr_inwentarzowy", "nr_seryjny", "status", "budynek", "pomieszczenie", "regal", "polka", "kod_qr", "uwagi"],
        "czesci": ["producent", "part_number", "nr_seryjny", "budynek", "pomieszczenie", "regal", "polka", "uwagi"],
    }
    if entity not in expected:
        abort(404)

    preview = []
    if request.method == "POST":
        file = request.files.get("file")
        commit = request.form.get("commit") == "1"
        if not file or not file.filename:
            flash("Wybierz plik CSV.", "danger")
            return redirect(request.url)
        try:
            rows = parse_csv(file, expected[entity])
            errors = validate_import(entity, rows)
            preview = rows[:10]
            if errors:
                for err in errors[:20]:
                    flash(err, "danger")
            elif commit:
                execute_import(entity, rows)
                flash("Import zakończony powodzeniem.", "success")
                return redirect(url_for("admin_panel"))
            else:
                flash("Walidacja zakończona powodzeniem. Możesz wykonać import właściwy.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("import_csv.html", title=f"Import CSV: {entity}", entity=entity, headers=expected[entity], preview=preview)


def validate_import(entity: str, rows: list[dict]) -> list[str]:
    errors: list[str] = []
    for idx, row in enumerate(rows, start=2):
        try:
            if entity == "lokalizacje":
                key = [row["budynek"], row["pomieszczenie"], row["regal"], row["polka"]]
                if not all(key):
                    raise ValueError("Każdy poziom lokalizacji jest wymagany.")
            elif entity == "modele_urzadzen":
                if DeviceModel.query.filter_by(manufacturer=row["producent"], model_code=row["model"]).first():
                    raise ValueError("Model urządzenia już istnieje.")
            elif entity == "typy_czesci":
                if PartType.query.filter_by(manufacturer=row["producent"], part_number=row["part_number"]).first():
                    raise ValueError("Typ części już istnieje.")
            elif entity == "urzadzenia":
                model = DeviceModel.query.filter_by(manufacturer=row["producent"], model_code=row["model"]).first()
                if not model:
                    raise ValueError("Nie istnieje model urządzenia.")
                ensure_unique_or_raise(row["nr_inwentarzowy"] or None, Device, "inventory_number", "Nr inwentarzowy")
                ensure_unique_or_raise((row["nr_seryjny"] or None), Device, "serial_number", "Numer seryjny")
                ensure_unique_or_raise(row["kod_qr"] or None, Device, "qr_code_value", "Kod QR")
                if row["status"] not in DEVICE_STATUSES:
                    raise ValueError("Nieprawidłowy status urządzenia.")
                if any(row[k] for k in ["budynek", "pomieszczenie", "regal", "polka"]):
                    shelf = find_shelf_by_path(row["budynek"], row["pomieszczenie"], row["regal"], row["polka"])
                    if not shelf:
                        raise ValueError("Nie istnieje wskazana lokalizacja.")
            elif entity == "czesci":
                ptype = PartType.query.filter_by(manufacturer=row["producent"], part_number=row["part_number"]).first()
                if not ptype:
                    raise ValueError("Nie istnieje typ części.")
                ensure_unique_or_raise((row["nr_seryjny"] or None), PartItem, "serial_number", "Numer seryjny")
                if any(row[k] for k in ["budynek", "pomieszczenie", "regal", "polka"]):
                    shelf = find_shelf_by_path(row["budynek"], row["pomieszczenie"], row["regal"], row["polka"])
                    if not shelf:
                        raise ValueError("Nie istnieje wskazana lokalizacja.")
        except Exception as exc:
            errors.append(f"Wiersz {idx}: {exc}")
    return errors


def execute_import(entity: str, rows: list[dict]) -> None:
    for row in rows:
        if entity == "lokalizacje":
            upsert_location(row["budynek"], row["pomieszczenie"], row["regal"], row["polka"])
        elif entity == "modele_urzadzen":
            db.session.add(DeviceModel(
                name=row["nazwa"], manufacturer=row["producent"], model_code=row["model"],
                description=row["opis"] or None, is_active=row["aktywny"].strip().lower() in ["1", "true", "tak", "yes"]
            ))
        elif entity == "typy_czesci":
            part_type = PartType(
                name=row["nazwa"], manufacturer=row["producent"], model_code=row["model"], part_number=row["part_number"],
                description=row["opis"] or None, is_active=row["aktywny"].strip().lower() in ["1", "true", "tak", "yes"]
            )
            db.session.add(part_type)
            db.session.flush()
            compat = [x.strip() for x in row["kompatybilne_modele"].split("|") if x.strip()]
            for compat_item in compat:
                manufacturer, model_code = [x.strip() for x in compat_item.split(":", 1)]
                model = DeviceModel.query.filter_by(manufacturer=manufacturer, model_code=model_code).first()
                db.session.add(PartCompatibility(part_type_id=part_type.id, device_model_id=model.id))
        elif entity == "urzadzenia":
            model = DeviceModel.query.filter_by(manufacturer=row["producent"], model_code=row["model"]).first()
            shelf = None
            if any(row[k] for k in ["budynek", "pomieszczenie", "regal", "polka"]):
                shelf = find_shelf_by_path(row["budynek"], row["pomieszczenie"], row["regal"], row["polka"])
            device = Device(
                device_model_id=model.id,
                inventory_number=row["nr_inwentarzowy"],
                serial_number=row["nr_seryjny"] or None,
                status=row["status"],
                shelf_id=shelf.id if shelf else None,
                qr_code_value=row["kod_qr"] or str(uuid.uuid4()),
                notes=row["uwagi"] or None,
            )
            db.session.add(device)
            db.session.flush()
            make_qr(device)
        elif entity == "czesci":
            ptype = PartType.query.filter_by(manufacturer=row["producent"], part_number=row["part_number"]).first()
            shelf = None
            if any(row[k] for k in ["budynek", "pomieszczenie", "regal", "polka"]):
                shelf = find_shelf_by_path(row["budynek"], row["pomieszczenie"], row["regal"], row["polka"])
            db.session.add(PartItem(
                part_type_id=ptype.id,
                serial_number=row["nr_seryjny"] or None,
                status="na stanie",
                shelf_id=shelf.id if shelf else None,
                notes=row["uwagi"] or None,
            ))
    db.session.commit()


@app.route("/szablony/<entity>.csv")
@role_required("admin")
def csv_template(entity: str):
    samples = {
        "lokalizacje": "budynek,pomieszczenie,regal,polka\nBudynek A,Magazyn 1,Regał 1,Półka 1\n",
        "modele_urzadzen": "nazwa,producent,model,opis,aktywny\nPompa infuzyjna,MedCorp,MI-200,Opis,1\n",
        "typy_czesci": "nazwa,producent,model,part_number,opis,aktywny,kompatybilne_modele\nAkumulator,MedCorp,BAT-1,P-001,Opis,1,MedCorp:MI-200\n",
        "urzadzenia": "producent,model,nr_inwentarzowy,nr_seryjny,status,budynek,pomieszczenie,regal,polka,kod_qr,uwagi\nMedCorp,MI-200,AST-000001,SN-001,aktywne,Budynek A,Magazyn 1,Regał 1,Półka 1,QR-001,Przykład\n",
        "czesci": "producent,part_number,nr_seryjny,budynek,pomieszczenie,regal,polka,uwagi\nMedCorp,P-001,PSN-001,Budynek A,Magazyn 1,Regał 1,Półka 1,Przykład\n",
    }
    if entity not in samples:
        abort(404)
    temp_dir = DATA_DIR / "templates_csv"
    temp_dir.mkdir(exist_ok=True)
    path = temp_dir / f"{entity}.csv"
    path.write_text(samples[entity], encoding="utf-8")
    return send_from_directory(temp_dir, f"{entity}.csv", as_attachment=True)


@app.route("/zalaczniki/<entity_type>/<int:entity_id>", methods=["POST"])
@role_required("admin", "edytor")
def attachment_upload(entity_type: str, entity_id: int):
    file = request.files.get("file")
    try:
        resolve_attachment_entity(entity_type, entity_id)
        if not file or not file.filename:
            raise ValueError("Nie wybrano pliku.")
        validate_uploaded_file(file)
        original = secure_filename(file.filename)
        stored = f"{uuid.uuid4().hex}_{original}"
        destination = UPLOAD_DIR / stored
        file.save(destination)
        detected_ext, detected_mime = sniff_file_type(file)
        db.session.add(Attachment(
            entity_type=entity_type,
            entity_id=entity_id,
            original_filename=original,
            stored_filename=stored,
            mime_type=detected_mime or file.mimetype,
            file_size=destination.stat().st_size,
        ))
        db.session.commit()
        flash("Załącznik został dodany.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/zalaczniki/<int:attachment_id>/pobierz")
@login_required
def attachment_download(attachment_id: int):
    attachment = active_query(Attachment).filter_by(id=attachment_id).first_or_404()
    return send_from_directory(UPLOAD_DIR, attachment.stored_filename, as_attachment=True, download_name=attachment.original_filename)


@app.route("/qr/<filename>")
@login_required
def qr_file(filename: str):
    return send_from_directory(QR_DIR, filename)


@app.route("/skanuj")
@login_required
def scan_qr():
    return render_template("scan.html", title="Skanuj QR")


@app.errorhandler(CSRFError)
def error_csrf(err):
    return render_template("error.html", title="Błąd CSRF", message=f"Nieprawidłowy token bezpieczeństwa: {err.description}"), 400


@app.errorhandler(429)
def error_429(_):
    return render_template("error.html", title="429", message="Zbyt wiele żądań. Spróbuj ponownie za chwilę."), 429


@app.errorhandler(403)
def error_403(_):
    return render_template("error.html", title="403", message="Brak uprawnień."), 403


@app.errorhandler(404)
def error_404(_):
    return render_template("error.html", title="404", message="Nie znaleziono strony."), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
