from datetime import date as dt_date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from functools import wraps

from flask import Blueprint, g, jsonify, request
import uuid
import os
import json
import hashlib
import hmac
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path

import jwt
import qrcode

import supabase
from api_features.bookings import register_booking_api_routes
from api_features.cms import register_cms_api_routes

ADULT_FEE = 50
CHILD_FEE = 30
QR_DIRECTORY = "qrcodes"
DEFAULT_GRACE_PERIOD_MINUTES = int(os.getenv("BOOKING_GRACE_PERIOD_MINUTES", "30"))
OPERATING_HOURS_START = os.getenv("OPERATING_HOURS_START", "09:00")
OPERATING_HOURS_END = os.getenv("OPERATING_HOURS_END", "17:00")
AUTO_CANCEL_NO_SHOW = os.getenv("NO_SHOW_AUTO_CANCEL", "1") != "0"
CMS_STORAGE_BUCKET = (os.getenv("SUPABASE_CMS_BUCKET") or "cms-media").strip() or "cms-media"
MAX_CMS_IMAGE_BYTES = int(os.getenv("CMS_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
ALLOWED_CMS_IMAGE_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def create_api_blueprint(supabase):
    api = Blueprint("api", __name__, url_prefix="/api")
    booking_base_columns = (
        "id,user_id,cottage_id,date,arrival_time,grace_period_minutes,checked_in,checked_in_at,checked_out_at,"
        "adults,children,num_people,total_amount,status,created_at,qr_code,"
        "cottages(name,price,capacity)"
    )
    booking_contact_columns = "full_name,email,phone"
    admin_roles = {"admin", "staff"}
    jwks_client = None
    jwks_client_url = ""

    def read_bearer_token():
        auth_header = str(request.headers.get("Authorization") or "").strip()
        if not auth_header:
            return None, ({"error": "Missing token"}, 401)

        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None, ({"error": "Invalid authorization header"}, 401)

        return token.strip(), None

    def get_jwks_client():
        nonlocal jwks_client, jwks_client_url
        supabase_url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        if not supabase_url:
            return None, ({"error": "SUPABASE_URL is not configured"}, 500)

        target_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        if not jwks_client or jwks_client_url != target_url:
            jwks_client = jwt.PyJWKClient(target_url)
            jwks_client_url = target_url

        return jwks_client, None

    def verify_supabase_jwt(token):
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            return None, ({"error": "Invalid token"}, 401)

        alg = str(unverified_header.get("alg") or "").upper()
        if not alg:
            return None, ({"error": "Invalid token"}, 401)

        try:
            if alg.startswith("HS"):
                jwt_secret = str(os.getenv("SUPABASE_JWT_SECRET") or "").strip()
                if not jwt_secret:
                    return None, ({"error": "SUPABASE_JWT_SECRET is not configured"}, 500)

                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=[alg],
                    options={"verify_aud": False},
                )
                return payload, None

            if alg.startswith("RS") or alg.startswith("ES"):
                client, client_error = get_jwks_client()
                if client_error:
                    return None, client_error

                signing_key = client.get_signing_key_from_jwt(token).key
                payload = jwt.decode(
                    token,
                    signing_key,
                    algorithms=[alg],
                    options={"verify_aud": False},
                )
                return payload, None
        except Exception:
            return None, ({"error": "Invalid token"}, 401)

        return None, ({"error": "Unsupported token algorithm"}, 401)

    def resolve_admin_context():
        cached = getattr(g, "_admin_context", None)
        if cached is not None:
            return cached, None

        token, token_error = read_bearer_token()
        if token_error:
            return None, token_error

        payload, verify_error = verify_supabase_jwt(token)
        if verify_error:
            return None, verify_error

        subject = str(payload.get("sub") or "").strip()
        email = str(payload.get("email") or "").strip().lower()

        app_metadata = payload.get("app_metadata") if isinstance(payload.get("app_metadata"), dict) else {}
        user_metadata = payload.get("user_metadata") if isinstance(payload.get("user_metadata"), dict) else {}

        claim_role = (
            str(app_metadata.get("role") or user_metadata.get("role") or payload.get("role") or "")
            .strip()
            .lower()
        )

        admin_id = subject or email
        resolved_email = email
        resolved_role = claim_role

        admin_row = None
        if email:
            try:
                result = (
                    supabase.table("admins")
                    .select("id,email,role")
                    .eq("email", email)
                    .limit(1)
                    .execute()
                )
                if result.data:
                    admin_row = result.data[0]
            except Exception:
                admin_row = None

        if admin_row:
            admin_id = str(admin_row.get("id") or admin_id)
            resolved_email = str(admin_row.get("email") or resolved_email or "").strip().lower()
            resolved_role = str(admin_row.get("role") or resolved_role or "").strip().lower()
        elif subject:
            # Optional role source: profiles table keyed by auth user id.
            try:
                profile_result = (
                    supabase.table("profiles")
                    .select("id,role")
                    .eq("id", subject)
                    .limit(1)
                    .execute()
                )
                if profile_result.data:
                    profile_row = profile_result.data[0]
                    admin_id = str(profile_row.get("id") or admin_id)
                    resolved_role = str(profile_row.get("role") or resolved_role or "").strip().lower()
            except Exception:
                pass

        if resolved_role not in admin_roles:
            return None, ({"error": "Unauthorized"}, 403)

        context = {
            "id": admin_id,
            "email": resolved_email,
            "role": resolved_role,
            "claims": payload,
        }
        setattr(g, "_admin_context", context)
        return context, None

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            _, error = resolve_admin_context()
            if error:
                body, status = error
                return jsonify(body), status
            return view(*args, **kwargs)

        return wrapped

    def role_required(*roles):
        allowed = {str(role).strip().lower() for role in roles}

        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                context, error = resolve_admin_context()
                if error:
                    body, status = error
                    return jsonify(body), status

                role = str(context.get("role") or "").strip().lower()
                if role not in allowed:
                    return jsonify({"error": "Forbidden"}), 403

                return view(*args, **kwargs)

            return wrapped

        return decorator

    def parse_booking_date(value):
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    def parse_arrival_time(value):
        if value is None:
            return None

        try:
            return datetime.strptime(str(value).strip(), "%H:%M").time()
        except (TypeError, ValueError):
            return None

    def parse_grace_period_minutes(value):
        if value is None or value == "":
            return DEFAULT_GRACE_PERIOD_MINUTES, None

        try:
            minutes = int(value)
        except (TypeError, ValueError):
            return None, {"error": "grace_period_minutes must be an integer"}

        if minutes < 0 or minutes > 240:
            return None, {"error": "grace_period_minutes must be between 0 and 240"}

        return minutes, None

    def validate_arrival_time(value):
        parsed_time = parse_arrival_time(value)
        if not parsed_time:
            return None, {"error": "arrival_time is required and must be in HH:MM format"}

        opening = parse_arrival_time(OPERATING_HOURS_START)
        closing = parse_arrival_time(OPERATING_HOURS_END)
        if opening and closing and (parsed_time < opening or parsed_time > closing):
            return None, {
                "error": f"arrival_time must be within operating hours ({OPERATING_HOURS_START} to {OPERATING_HOURS_END})"
            }

        return parsed_time.strftime("%H:%M"), None

    def to_arrival_deadline(booking_date_value, arrival_time_value, grace_minutes):
        booking_date = parse_booking_date(booking_date_value)
        arrival_time = parse_arrival_time(arrival_time_value)
        if not booking_date or not arrival_time:
            return None

        arrival_dt = datetime.combine(booking_date, arrival_time)
        return arrival_dt + timedelta(minutes=max(0, int(grace_minutes or 0)))

    def evaluate_arrival_window(booking, now=None):
        now_dt = now or datetime.now()
        status = canonical_booking_status(booking.get("status"))
        deadline = to_arrival_deadline(
            booking.get("date"),
            booking.get("arrival_time"),
            booking.get("grace_period_minutes"),
        )

        if status == "checked_in" or booking.get("checked_in"):
            return {
                "arrival_window_status": "arrived",
                "arrival_deadline": deadline.isoformat() if deadline else None,
                "minutes_to_deadline": None,
            }

        if status == "no_show":
            return {
                "arrival_window_status": "no_show",
                "arrival_deadline": deadline.isoformat() if deadline else None,
                "minutes_to_deadline": None,
            }

        if status != "confirmed" or not deadline:
            return {
                "arrival_window_status": "pending",
                "arrival_deadline": deadline.isoformat() if deadline else None,
                "minutes_to_deadline": None,
            }

        minutes_to_deadline = int((deadline - now_dt).total_seconds() // 60)
        arrival_dt = deadline - timedelta(minutes=max(0, int(booking.get("grace_period_minutes") or 0)))
        if now_dt < arrival_dt:
            window = "on_the_way"
        elif now_dt <= deadline:
            window = "late"
        else:
            window = "no_show"

        return {
            "arrival_window_status": window,
            "arrival_deadline": deadline.isoformat(),
            "minutes_to_deadline": minutes_to_deadline,
        }

    def auto_mark_no_shows():
        if not AUTO_CANCEL_NO_SHOW:
            return 0

        today = str(dt_date.today())
        try:
            candidates = (
                supabase.table("bookings")
                .select("id,date,arrival_time,grace_period_minutes,status,checked_in")
                .eq("status", "confirmed")
                .lte("date", today)
                .execute()
            )
        except Exception:
            return 0

        changed = 0
        now_dt = datetime.now()
        for row in (candidates.data or []):
            if row.get("checked_in"):
                continue

            deadline = to_arrival_deadline(row.get("date"), row.get("arrival_time"), row.get("grace_period_minutes"))
            if not deadline or now_dt <= deadline:
                continue

            (
                supabase.table("bookings")
                .update({"status": "no_show"})
                .eq("id", row.get("id"))
                .eq("status", "confirmed")
                .execute()
            )
            changed += 1

        return changed

    def build_qr_checksum(booking_id, booking_date):
        secret = os.getenv("QR_SIGNING_SECRET") or os.getenv("SUPABASE_SECRET_KEY") or "bagasbas-local-secret"
        material = f"{booking_id}:{booking_date}:{secret}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]

    def generate_booking_qr(booking_id, booking_date):
        payload = {
            "booking_id": booking_id,
            "date": str(booking_date),
            "checksum": build_qr_checksum(booking_id, booking_date),
        }
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        project_root = Path(__file__).resolve().parent
        qr_dir = project_root / "static" / QR_DIRECTORY
        qr_dir.mkdir(parents=True, exist_ok=True)

        qr_filename = f"{booking_id}.png"
        qr_path = qr_dir / qr_filename

        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(payload_str)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        image.save(qr_path)

        return f"{QR_DIRECTORY}/{qr_filename}"

    def build_qr_public_url(qr_code_value):
        if not qr_code_value:
            return None

        normalized = str(qr_code_value).lstrip("/")
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized

        public_base = (os.getenv("PUBLIC_BASE_URL") or request.url_root or "").rstrip("/")
        if not public_base:
            return f"/static/{normalized}"

        return f"{public_base}/static/{normalized}"

    def send_booking_confirmation_email(booking):
        recipient = (booking.get("email") or "").strip()
        if not recipient:
            return False, "Booking has no email address"

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        smtp_from = os.getenv("SMTP_FROM_EMAIL") or smtp_user
        smtp_use_tls = os.getenv("SMTP_USE_TLS", "1") != "0"

        if not smtp_host or not smtp_user or not smtp_password or not smtp_from:
            return False, "SMTP is not fully configured"

        qr_url = build_qr_public_url(booking.get("qr_code"))
        qr_src = qr_url or ""
        qr_code_value = booking.get("qr_code")
        qr_cid = "booking-qr"
        qr_file_path = None

        if qr_code_value:
            normalized = str(qr_code_value).lstrip("/")
            if normalized.startswith("static/"):
                normalized = normalized[len("static/") :]
            qr_file_path = Path(__file__).resolve().parent / "static" / normalized
            if qr_file_path.exists():
                qr_src = f"cid:{qr_cid}"

        html_body = f"""
        <h2>Booking Confirmed</h2>
        <p>Your reservation has been approved.</p>
        <ul>
          <li><strong>Name:</strong> {booking.get('full_name') or '-'}</li>
          <li><strong>Date:</strong> {booking.get('date') or '-'}</li>
          <li><strong>Cottage:</strong> {booking.get('cottage_name') or '-'}</li>
          <li><strong>Guests:</strong> {booking.get('num_people') or 0}</li>
          <li><strong>Total:</strong> P{booking.get('total_amount') or 0}</li>
        </ul>
        <p>Show this QR code upon arrival:</p>
        <p><img src=\"{qr_src}\" width=\"200\" alt=\"Booking QR\"></p>
        <p>Present this QR code at the entrance for verification.</p>
        <p>Thank you.</p>
        """

        text_body = (
            "Booking Confirmed\n"
            "Your reservation has been approved.\n\n"
            f"Name: {booking.get('full_name') or '-'}\n"
            f"Date: {booking.get('date') or '-'}\n"
            f"Cottage: {booking.get('cottage_name') or '-'}\n"
            f"Guests: {booking.get('num_people') or 0}\n"
            f"Total: P{booking.get('total_amount') or 0}\n\n"
            f"QR Code: {qr_url or 'Embedded in HTML email'}\n"
            "Present this QR code at the entrance for verification."
        )

        message = MIMEMultipart("related")
        alternative = MIMEMultipart("alternative")
        message.attach(alternative)
        message["Subject"] = f"Booking Confirmed - {booking.get('date') or ''}"
        message["From"] = smtp_from
        message["To"] = recipient

        alternative.attach(MIMEText(text_body, "plain"))
        alternative.attach(MIMEText(html_body, "html"))

        if qr_file_path and qr_file_path.exists():
            with open(qr_file_path, "rb") as qr_file:
                qr_image_part = MIMEImage(qr_file.read(), _subtype="png")
            qr_image_part.add_header("Content-ID", f"<{qr_cid}>")
            qr_image_part.add_header("Content-Disposition", "inline", filename="booking-qr.png")
            message.attach(qr_image_part)

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, [recipient], message.as_string())
            return True, None
        except Exception as exc:
            return False, str(exc)

    def send_booking_rejection_email(booking):
        recipient = (booking.get("email") or "").strip()
        if not recipient:
            return False, "Booking has no email address"

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        smtp_from = os.getenv("SMTP_FROM_EMAIL") or smtp_user
        smtp_use_tls = os.getenv("SMTP_USE_TLS", "1") != "0"

        if not smtp_host or not smtp_user or not smtp_password or not smtp_from:
            return False, "SMTP is not fully configured"

        html_body = f"""
        <h2>Booking Update</h2>
        <p>We are sorry to inform you that your reservation request has been rejected.</p>
        <ul>
          <li><strong>Name:</strong> {booking.get('full_name') or '-'}</li>
          <li><strong>Date:</strong> {booking.get('date') or '-'}</li>
          <li><strong>Cottage:</strong> {booking.get('cottage_name') or '-'}</li>
          <li><strong>Guests:</strong> {booking.get('num_people') or 0}</li>
          <li><strong>Total:</strong> P{booking.get('total_amount') or 0}</li>
        </ul>
        <p>You may try booking another date or contact support for assistance.</p>
        <p>Thank you for understanding.</p>
        """

        text_body = (
            "Booking Update\n"
            "We are sorry to inform you that your reservation request has been rejected.\n\n"
            f"Name: {booking.get('full_name') or '-'}\n"
            f"Date: {booking.get('date') or '-'}\n"
            f"Cottage: {booking.get('cottage_name') or '-'}\n"
            f"Guests: {booking.get('num_people') or 0}\n"
            f"Total: P{booking.get('total_amount') or 0}\n\n"
            "You may try booking another date or contact support for assistance."
        )

        message = MIMEMultipart("alternative")
        message["Subject"] = f"Booking Rejected - {booking.get('date') or ''}"
        message["From"] = smtp_from
        message["To"] = recipient
        message.attach(MIMEText(text_body, "plain"))
        message.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, [recipient], message.as_string())
            return True, None
        except Exception as exc:
            return False, str(exc)

    def fetch_booking_by_id(booking_id):
        booking_result = (
            supabase.table("bookings")
            .select(
                "id,user_id,cottage_id,date,arrival_time,grace_period_minutes,checked_in,checked_in_at,checked_out_at,"
                "adults,children,num_people,total_amount,status,created_at,"
                "full_name,email,phone,qr_code,cottages(name,price,capacity)"
            )
            .eq("id", booking_id)
            .limit(1)
            .execute()
        )

        if not booking_result.data:
            return None

        return serialize_booking(booking_result.data[0])

    def ensure_booking_qr(booking):
        if booking.get("qr_code"):
            return booking.get("qr_code"), False, None

        qr_code_value = generate_booking_qr(booking["id"], booking["date"])
        (
            supabase.table("bookings")
            .update({"qr_code": qr_code_value})
            .eq("id", booking["id"])
            .execute()
        )
        return qr_code_value, True, None

    def resolve_admin_id(data=None):
        payload_admin = (data or {}).get("admin_id") if isinstance(data, dict) else None
        header_admin = request.headers.get("X-Admin-Id")
        context, _ = resolve_admin_context()
        token_admin = (context or {}).get("id")
        return str(header_admin or payload_admin or token_admin or "admin-local").strip()

    def validate_scanner_authorization(data=None):
        configured_key = (os.getenv("SCANNER_API_KEY") or "bagasbas-scanner-key").strip()

        payload_key = ""
        if isinstance(data, dict):
            payload_key = str(data.get("scanner_key") or "").strip()
        header_key = str(request.headers.get("X-Scanner-Key") or "").strip()
        provided_key = header_key or payload_key

        if not provided_key or not hmac.compare_digest(provided_key, configured_key):
            return jsonify({"status": "UNAUTHORIZED", "error": "Scanner authorization failed"}), 401

        return None

    def extract_booking_id_from_scan_payload(data):
        if not isinstance(data, dict):
            return None, None, {"status": "INVALID", "error": "JSON body is required"}

        scan_data = data.get("scan_data")
        if scan_data is None:
            booking_id = data.get("booking_id")
            if booking_id:
                return str(booking_id).strip(), None, None
            return None, None, {"status": "INVALID", "error": "scan_data or booking_id is required"}

        if isinstance(scan_data, dict):
            payload = scan_data
        else:
            try:
                payload = json.loads(str(scan_data).strip())
            except (TypeError, ValueError):
                return None, None, {"status": "INVALID", "error": "QR payload is not valid JSON"}

        booking_id = str(payload.get("booking_id") or "").strip()
        token_date = str(payload.get("date") or "").strip()
        checksum = str(payload.get("checksum") or "").strip()

        if not booking_id or not token_date or not checksum:
            return None, None, {"status": "INVALID", "error": "QR payload is incomplete"}

        expected = build_qr_checksum(booking_id, token_date)
        if not hmac.compare_digest(checksum, expected):
            return None, None, {"status": "INVALID", "error": "QR signature verification failed"}

        return booking_id, token_date, None

    def write_scan_log(booking_id, result, admin_id):
        try:
            supabase.table("scan_logs").insert(
                {
                    "id": str(uuid.uuid4()),
                    "booking_id": booking_id,
                    "scanned_at": datetime.utcnow().isoformat(),
                    "result": result,
                    "admin_id": admin_id,
                }
            ).execute()
        except Exception:
            # Do not block entrance workflow if logging table is unavailable.
            return

    def approve_booking_workflow(booking_id):
        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        booking = fetch_booking_by_id(booking_id)
        if not booking:
            return jsonify({"error": "Booking not found"}), 404

        current_status = (booking.get("status") or "").strip().lower()
        if current_status == "cancelled":
            return jsonify({"error": "Cannot approve a cancelled booking"}), 400

        warnings = []
        qr_generated = False
        email_sent = False
        transitioned_to_confirmed = False

        if current_status == "pending":
            (
                supabase.table("bookings")
                .update({"status": "confirmed"})
                .eq("id", booking_id)
                .execute()
            )
            booking["status"] = "confirmed"
            transitioned_to_confirmed = True

        if booking.get("status") == "confirmed":
            try:
                qr_code_value, qr_generated, _ = ensure_booking_qr(booking)
                booking["qr_code"] = qr_code_value
            except Exception as exc:
                warnings.append(f"QR generation/storage failed: {exc}")

            if transitioned_to_confirmed and booking.get("email"):
                email_sent, email_error = send_booking_confirmation_email(booking)
                if email_error:
                    warnings.append(f"Email not sent: {email_error}")
            elif transitioned_to_confirmed:
                warnings.append("Booking has no email; confirmation email skipped")

        latest = fetch_booking_by_id(booking_id) or booking
        is_repeat_approval = canonical_booking_status(current_status) in ["confirmed", "checked_in", "completed"]
        message = "Booking already confirmed" if is_repeat_approval else "Booking approved"

        return jsonify(
            {
                "message": message,
                "booking": latest,
                "qr_generated": qr_generated,
                "email_sent": email_sent,
                "warnings": warnings,
            }
        )

    def reject_booking_workflow(booking_id):
        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        booking = fetch_booking_by_id(booking_id)
        if not booking:
            return jsonify({"error": "Booking not found"}), 404

        current_status = (booking.get("status") or "").strip().lower()
        if current_status != "pending":
            return jsonify({"error": f"Cannot update booking with status '{booking.get('status')}'"}), 400

        (
            supabase.table("bookings")
            .update({"status": "cancelled"})
            .eq("id", booking_id)
            .execute()
        )

        warnings = []
        email_sent = False
        if booking.get("email"):
            email_sent, email_error = send_booking_rejection_email(booking)
            if email_error:
                warnings.append(f"Email not sent: {email_error}")
        else:
            warnings.append("Booking has no email; rejection email skipped")

        latest = fetch_booking_by_id(booking_id) or {**booking, "status": "cancelled"}
        return jsonify(
            {
                "message": "Booking marked as cancelled",
                "booking": latest,
                "email_sent": email_sent,
                "warnings": warnings,
            }
        )

    def validate_payload(data, required_fields):
        if not data:
            return {"error": "JSON body is required"}

        missing = [key for key in required_fields if key not in data]
        if missing:
            return {"error": "Missing required fields", "missing": missing}

        return None

    def check_availability_logic(cottage_id, date, num_people):
        existing = (
            supabase.table("bookings")
            .select("id,status")
            .eq("cottage_id", cottage_id)
            .eq("date", date)
            .in_("status", ["confirmed", "checked_in"])
            .execute()
        )

        blocking_bookings = [row for row in (existing.data or []) if is_blocking_status(row.get("status"))]
        if blocking_bookings:
            return {"available": False, "reason": "Cottage already booked"}

        return {"available": True}

    def canonical_booking_status(value):
        normalized = (value or "").strip().lower()
        if normalized == "arrived":
            return "checked_in"
        if normalized == "used":
            return "completed"
        return normalized

    def parse_non_negative_int(field_name, value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None, {"error": f"{field_name} must be an integer"}

        if parsed < 0:
            return None, {"error": f"{field_name} cannot be negative"}

        return parsed, None

    def is_blocking_status(status):
        # Only confirmed and checked-in bookings should block cottage availability.
        normalized = canonical_booking_status(status)
        return normalized in ["confirmed", "checked_in"]

    def extract_people_count(data):
        if "adults" in data or "children" in data:
            adults, err = parse_non_negative_int("adults", data.get("adults", 0))
            if err:
                return None, err

            children, err = parse_non_negative_int("children", data.get("children", 0))
            if err:
                return None, err

            num_people = adults + children
            if num_people <= 0:
                return None, {"error": "At least one guest is required"}

            return num_people, None

        if "num_people" in data:
            num_people, err = parse_non_negative_int("num_people", data.get("num_people", 0))
            if err:
                return None, err

            if num_people <= 0:
                return None, {"error": "At least one guest is required"}

            return num_people, None

        return None, {"error": "Provide adults/children or num_people"}

    def validate_uuid(field_name, value):
        try:
            uuid.UUID(str(value))
        except (TypeError, ValueError):
            return {"error": f"{field_name} must be a valid UUID"}

        return None

    def map_db_error(exc):
        error_text = str(exc)

        if "'code': '22P02'" in error_text:
            return 400, {"error": "Invalid UUID/value in request payload", "reason": error_text}

        if "'code': '23503'" in error_text:
            return 400, {"error": "Invalid reference value", "reason": error_text}

        if "'code': '42501'" in error_text:
            return 403, {
                "error": "Permission denied by database policy",
                "reason": "RLS policy blocks this operation for current API key",
            }

        if "Could not find the 'adults' column" in error_text or "Could not find the 'children' column" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Run the bookings table migration to add adults and children columns",
            }

        if (
            "Could not find the 'full_name' column" in error_text
            or "Could not find the 'email' column" in error_text
            or "Could not find the 'phone' column" in error_text
            or "Could not find the 'qr_code' column" in error_text
        ):
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Add full_name, email, phone, and qr_code columns to bookings table",
            }

        if (
            "Could not find the 'arrival_time' column" in error_text
            or "Could not find the 'grace_period_minutes' column" in error_text
            or "Could not find the 'checked_in' column" in error_text
            or "Could not find the 'checked_in_at' column" in error_text
            or "Could not find the 'checked_out_at' column" in error_text
        ):
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Run migration to add arrival_time, grace_period_minutes, checked_in, checked_in_at, and checked_out_at columns",
            }

        if "relation \"public.scan_logs\" does not exist" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Create scan_logs table before using scanner audit trail",
            }

        if "relation \"public.events\" does not exist" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Create events table before using CMS events module",
            }

        if "relation \"public.gallery\" does not exist" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Create gallery table before using CMS gallery module",
            }

        if "relation \"public.settings\" does not exist" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Create settings table before using CMS/booking mode toggles",
            }

        if "Could not find the table 'public.settings' in the schema cache" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "The settings table is missing from Supabase schema cache. Create it, then refresh API schema cache.",
            }

        if "Could not find the 'booking_enabled' column" in error_text or "Could not find the 'updated_at' column" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Run the settings migration to add booking_enabled and updated_at columns.",
            }

        if "relation \"public.inquiries\" does not exist" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Create inquiries table before using inquiry module",
            }

        if "Could not find the table 'public.inquiries' in the schema cache" in error_text:
            return 500, {
                "error": "Database schema mismatch",
                "reason": "The inquiries table is missing from Supabase schema cache. Create it, then refresh API schema cache.",
            }

        if (
            "Could not find the 'name' column" in error_text
            or "Could not find the 'preferred_date' column" in error_text
            or "Could not find the 'message' column" in error_text
            or "Could not find the 'status' column" in error_text
            or "Could not find the 'updated_at' column" in error_text
        ):
            return 500, {
                "error": "Database schema mismatch",
                "reason": "Run the inquiries migration to add name, preferred_date, message, status, and updated_at columns.",
            }

        if "Temporary failure in name resolution" in error_text or "ConnectError" in error_text:
            return 503, {
                "error": "Supabase unavailable",
                "reason": "The app cannot reach Supabase right now. Check internet access, DNS, and the SUPABASE_URL hostname.",
            }

        return 500, {"error": "Request failed", "reason": error_text}

    def serialize_booking(row):
        cottage = row.get("cottages") or {}
        if isinstance(cottage, list):
            cottage = cottage[0] if cottage else {}

        payload = {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "full_name": row.get("full_name"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "cottage_id": row.get("cottage_id"),
            "date": row.get("date"),
            "arrival_time": row.get("arrival_time"),
            "grace_period_minutes": row.get("grace_period_minutes", DEFAULT_GRACE_PERIOD_MINUTES),
            "checked_in": bool(row.get("checked_in")),
            "checked_in_at": row.get("checked_in_at"),
            "checked_out_at": row.get("checked_out_at"),
            "adults": row.get("adults", 0),
            "children": row.get("children", 0),
            "num_people": row.get("num_people", 0),
            "total_amount": row.get("total_amount", 0),
            "status": canonical_booking_status(row.get("status")),
            "qr_code": row.get("qr_code"),
            "created_at": row.get("created_at"),
            "cottage_name": cottage.get("name", "Unknown Cottage"),
            "cottage_price": cottage.get("price", 0),
            "cottage_capacity": cottage.get("capacity"),
        }
        payload.update(evaluate_arrival_window(payload))
        return payload

    def fetch_admin_bookings(date_filter=None, status_filter=None, search_term=None):
        auto_mark_no_shows()
        select_columns = booking_base_columns + "," + booking_contact_columns

        def run_query(columns):
            query = (
                supabase.table("bookings")
                .select(columns)
                .order("date", desc=True)
            )

            if date_filter:
                query = query.eq("date", date_filter)

            if search_term:
                term = search_term.strip().replace("%", "")
                if term:
                    search_filters = [f"id.ilike.%{term}%", f"user_id.ilike.%{term}%"]
                    if "full_name" in columns:
                        search_filters.append(f"full_name.ilike.%{term}%")
                    query = query.or_(",".join(search_filters))

            return query.execute()

        try:
            result = run_query(select_columns)
        except Exception as exc:
            error_text = str(exc)
            missing_columns = [
                "arrival_time",
                "grace_period_minutes",
                "checked_in",
                "checked_in_at",
                "checked_out_at",
                "full_name",
                "email",
                "phone",
                "qr_code",
            ]
            if any(f"Could not find the '{col}' column" in error_text for col in missing_columns):
                fallback_columns = select_columns
                for col in missing_columns:
                    if f"Could not find the '{col}' column" in error_text:
                        fallback_columns = fallback_columns.replace(f",{col}", "").replace(f"{col},", "")
                result = run_query(fallback_columns)
            else:
                raise

        bookings = [serialize_booking(row) for row in (result.data or [])]
        normalized_filter = canonical_booking_status(status_filter)
        if normalized_filter and normalized_filter != "all":
            bookings = [row for row in bookings if canonical_booking_status(row.get("status")) == normalized_filter]
        return bookings

    def validate_contact_fields(full_name, email, phone):
        full_name = (full_name or "").strip()
        email = (email or "").strip().lower()
        phone = (phone or "").strip()

        if not full_name:
            return None, None, None, {"error": "full_name is required"}

        if len(full_name) > 120:
            return None, None, None, {"error": "full_name is too long"}

        if not email or "@" not in email or "." not in email.split("@")[-1]:
            return None, None, None, {"error": "email must be a valid email address"}

        compact_phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if phone.startswith("+"):
            compact_phone = "+" + compact_phone[1:]

        if not phone or len(compact_phone.replace("+", "")) < 7:
            return None, None, None, {"error": "phone must be a valid phone number"}

        allowed = set("+0123456789-() ")
        if any(ch not in allowed for ch in phone):
            return None, None, None, {"error": "phone contains invalid characters"}

        return full_name, email, phone, None

    def validate_cottage_status(value):
        status = (value or "active").strip().lower()
        if status not in ["active", "inactive"]:
            return None, {"error": "status must be active or inactive"}
        return status, None

    def parse_bool(value, default=False):
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def parse_required_bool(field_name, value):
        if isinstance(value, bool):
            return value, None

        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "n", "off"}:
            return False, None

        return None, {"error": f"{field_name} must be a boolean"}

    def get_settings_record():
        try:
            result = (
                supabase.table("settings")
                .select("id,booking_enabled,updated_at")
                .order("id")
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]
        except Exception:
            return None

        return None

    def is_booking_enabled():
        default_value = parse_bool(os.getenv("BOOKING_ENABLED_DEFAULT", "0"), default=False)
        settings_record = get_settings_record()
        if settings_record is None:
            return default_value

        return bool(settings_record.get("booking_enabled"))

    def booking_disabled_response():
        return jsonify({"error": "Booking system is currently disabled", "mode": "cms"}), 403

    def require_booking_enabled():
        if is_booking_enabled():
            return None
        return booking_disabled_response()

    def normalize_event_tags(value):
        if value is None:
            return [], None

        if isinstance(value, str):
            source = value.split(",")
        elif isinstance(value, list):
            source = value
        else:
            return None, {"error": "tags must be an array or comma-separated string"}

        tags = []
        for item in source:
            label = str(item or "").strip()
            if not label:
                continue

            if len(label) > 32:
                label = label[:32]

            if label not in tags:
                tags.append(label)

        return tags[:8], None

    def parse_optional_date(field_name, value):
        if value in [None, ""]:
            return None, None

        parsed = parse_booking_date(value)
        if not parsed:
            return None, {"error": f"{field_name} must be in YYYY-MM-DD format"}

        return parsed.isoformat(), None

    def validate_inquiry_payload(data):
        if not isinstance(data, dict):
            return None, {"error": "JSON body is required"}

        full_name, email, phone, contact_error = validate_contact_fields(
            data.get("name"),
            data.get("email"),
            data.get("phone"),
        )
        if contact_error:
            return None, contact_error

        preferred_date, date_error = parse_optional_date("preferred_date", data.get("preferred_date"))
        if date_error:
            return None, date_error

        message = str(data.get("message") or "").strip()
        if not message:
            return None, {"error": "message is required"}
        if len(message) > 3000:
            return None, {"error": "message is too long"}

        return {
            "name": full_name,
            "email": email,
            "phone": phone,
            "preferred_date": preferred_date,
            "message": message,
            "status": "new",
        }, None

    def validate_event_payload(data, partial=False):
        if not isinstance(data, dict):
            return None, {"error": "JSON body is required"}

        payload = {}

        if not partial or "title" in data:
            title = str(data.get("title") or "").strip()
            if not title:
                return None, {"error": "title is required"}
            if len(title) > 140:
                return None, {"error": "title is too long"}
            payload["title"] = title

        if "description" in data or not partial:
            description = str(data.get("description") or "").strip()
            if len(description) > 3000:
                return None, {"error": "description is too long"}
            payload["description"] = description

        if "image_url" in data or not partial:
            image_url = str(data.get("image_url") or "").strip()
            payload["image_url"] = image_url

        if "location" in data or not partial:
            location = str(data.get("location") or "").strip()
            if len(location) > 180:
                return None, {"error": "location is too long"}
            payload["location"] = location

        if "event_date" in data or not partial:
            parsed_date, date_error = parse_optional_date("event_date", data.get("event_date"))
            if date_error:
                return None, date_error
            payload["event_date"] = parsed_date

        if "tags" in data or not partial:
            tags, tags_error = normalize_event_tags(data.get("tags"))
            if tags_error:
                return None, tags_error
            payload["tags"] = tags

        if "is_featured" in data or not partial:
            payload["is_featured"] = parse_bool(data.get("is_featured"), default=False)

        if "is_published" in data or not partial:
            payload["is_published"] = parse_bool(data.get("is_published"), default=False)

        if partial and not payload:
            return None, {"error": "No valid fields provided for update"}

        return payload, None

    def validate_gallery_payload(data, partial=False):
        if not isinstance(data, dict):
            return None, {"error": "JSON body is required"}

        payload = {}

        if not partial or "image_url" in data:
            image_url = str(data.get("image_url") or "").strip()
            if not image_url:
                return None, {"error": "image_url is required"}
            if len(image_url) > 1024:
                return None, {"error": "image_url is too long"}
            payload["image_url"] = image_url

        if "caption" in data or not partial:
            caption = str(data.get("caption") or "").strip()
            if len(caption) > 280:
                return None, {"error": "caption is too long"}
            payload["caption"] = caption

        if "category" in data or not partial:
            category = str(data.get("category") or "General").strip() or "General"
            if len(category) > 80:
                return None, {"error": "category is too long"}
            payload["category"] = category

        if "is_published" in data or not partial:
            payload["is_published"] = parse_bool(data.get("is_published"), default=False)

        if partial and not payload:
            return None, {"error": "No valid fields provided for update"}

        return payload, None

    def update_booking_status(booking_id, new_status):
        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        if new_status not in ["confirmed", "cancelled"]:
            return jsonify({"error": "status must be confirmed or cancelled"}), 400

        existing = (
            supabase.table("bookings")
            .select("id,status")
            .eq("id", booking_id)
            .limit(1)
            .execute()
        )

        if not existing.data:
            return jsonify({"error": "Booking not found"}), 404

        current_status = existing.data[0].get("status")
        if current_status != "pending":
            return jsonify({"error": f"Cannot update booking with status '{current_status}'"}), 400

        (
            supabase.table("bookings")
            .update({"status": new_status})
            .eq("id", booking_id)
            .execute()
        )

        updated = (
            supabase.table("bookings")
            .select("id,user_id,cottage_id,date,adults,children,num_people,total_amount,status,created_at,cottages(name,price,capacity)")
            .eq("id", booking_id)
            .limit(1)
            .execute()
        )

        payload = serialize_booking(updated.data[0]) if updated.data else {"id": booking_id, "status": new_status}
        return jsonify({"message": f"Booking marked as {new_status}", "booking": payload})

    

    @api.route("/admin/me", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_me():
        context, error = resolve_admin_context()
        if error:
            body, status = error
            return jsonify(body), status

        return jsonify(
            {
                "authenticated": True,
                "admin": {
                    "id": context.get("id"),
                    "email": context.get("email"),
                    "role": context.get("role"),
                },
            }
        )

    register_cms_api_routes(
        api,
        {
            "supabase": supabase,
            "admin_required": admin_required,
            "role_required": role_required,
            "map_db_error": map_db_error,
            "get_settings_record": get_settings_record,
            "parse_bool": parse_bool,
            "validate_inquiry_payload": validate_inquiry_payload,
            "parse_required_bool": parse_required_bool,
            "validate_uuid": validate_uuid,
            "validate_event_payload": validate_event_payload,
            "validate_gallery_payload": validate_gallery_payload,
            "CMS_STORAGE_BUCKET": CMS_STORAGE_BUCKET,
            "MAX_CMS_IMAGE_BYTES": MAX_CMS_IMAGE_BYTES,
            "ALLOWED_CMS_IMAGE_MIME": ALLOWED_CMS_IMAGE_MIME,
        },
    )

    register_booking_api_routes(
        api,
        {
            "supabase": supabase,
            "admin_required": admin_required,
            "role_required": role_required,
            "require_booking_enabled": require_booking_enabled,
            "validate_payload": validate_payload,
            "auto_mark_no_shows": auto_mark_no_shows,
            "extract_people_count": extract_people_count,
            "validate_uuid": validate_uuid,
            "check_availability_logic": check_availability_logic,
            "map_db_error": map_db_error,
            "validate_arrival_time": validate_arrival_time,
            "parse_grace_period_minutes": parse_grace_period_minutes,
            "validate_contact_fields": validate_contact_fields,
            "parse_non_negative_int": parse_non_negative_int,
            "is_blocking_status": is_blocking_status,
            "ADULT_FEE": ADULT_FEE,
            "CHILD_FEE": CHILD_FEE,
            "update_booking_status": update_booking_status,
            "fetch_admin_bookings": fetch_admin_bookings,
            "parse_booking_date": parse_booking_date,
            "canonical_booking_status": canonical_booking_status,
            "validate_cottage_status": validate_cottage_status,
            "fetch_booking_by_id": fetch_booking_by_id,
            "approve_booking_workflow": approve_booking_workflow,
            "ensure_booking_qr": ensure_booking_qr,
            "send_booking_confirmation_email": send_booking_confirmation_email,
            "validate_scanner_authorization": validate_scanner_authorization,
            "extract_booking_id_from_scan_payload": extract_booking_id_from_scan_payload,
            "resolve_admin_id": resolve_admin_id,
            "write_scan_log": write_scan_log,
            "to_arrival_deadline": to_arrival_deadline,
            "AUTO_CANCEL_NO_SHOW": AUTO_CANCEL_NO_SHOW,
            "reject_booking_workflow": reject_booking_workflow,
        },
    )

    return api
