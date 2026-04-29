from datetime import date as dt_date
from datetime import datetime
import os
import uuid

from flask import jsonify, request


def register_booking_api_routes(api, ctx):
    supabase = ctx["supabase"]
    admin_required = ctx["admin_required"]
    role_required = ctx["role_required"]
    require_booking_enabled = ctx["require_booking_enabled"]
    validate_payload = ctx["validate_payload"]
    auto_mark_no_shows = ctx["auto_mark_no_shows"]
    extract_people_count = ctx["extract_people_count"]
    validate_uuid = ctx["validate_uuid"]
    check_availability_logic = ctx["check_availability_logic"]
    map_db_error = ctx["map_db_error"]
    validate_arrival_time = ctx["validate_arrival_time"]
    parse_grace_period_minutes = ctx["parse_grace_period_minutes"]
    validate_contact_fields = ctx["validate_contact_fields"]
    parse_non_negative_int = ctx["parse_non_negative_int"]
    is_blocking_status = ctx["is_blocking_status"]
    ADULT_FEE = ctx["ADULT_FEE"]
    CHILD_FEE = ctx["CHILD_FEE"]
    update_booking_status = ctx["update_booking_status"]
    fetch_admin_bookings = ctx["fetch_admin_bookings"]
    parse_booking_date = ctx["parse_booking_date"]
    canonical_booking_status = ctx["canonical_booking_status"]
    validate_cottage_status = ctx["validate_cottage_status"]
    fetch_booking_by_id = ctx["fetch_booking_by_id"]
    approve_booking_workflow = ctx["approve_booking_workflow"]
    ensure_booking_qr = ctx["ensure_booking_qr"]
    send_booking_confirmation_email = ctx["send_booking_confirmation_email"]
    validate_scanner_authorization = ctx["validate_scanner_authorization"]
    extract_booking_id_from_scan_payload = ctx["extract_booking_id_from_scan_payload"]
    resolve_admin_id = ctx["resolve_admin_id"]
    write_scan_log = ctx["write_scan_log"]
    to_arrival_deadline = ctx["to_arrival_deadline"]
    AUTO_CANCEL_NO_SHOW = ctx["AUTO_CANCEL_NO_SHOW"]
    reject_booking_workflow = ctx["reject_booking_workflow"]

    @api.route("/check-availability", methods=["GET", "POST"])
    def check_availability():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        if request.method == "GET":
            return jsonify(
                {
                    "endpoint": "/api/check-availability",
                    "method": "POST",
                    "example_payload": {
                        "cottage_id": "PUT_ID_HERE",
                        "date": "2026-04-20",
                        "adults": 2,
                        "children": 1,
                    },
                }
            )

        data = request.json
        auto_mark_no_shows()
        validation_error = validate_payload(data, ["cottage_id", "date"])
        if validation_error:
            return jsonify(validation_error), 400

        cottage_id = data["cottage_id"]
        date = data["date"]
        num_people, people_error = extract_people_count(data)
        if people_error:
            return jsonify(people_error), 400

        uuid_error = validate_uuid("cottage_id", cottage_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            result = check_availability_logic(cottage_id, date, num_people)
            if result.get("available"):
                result["num_people"] = num_people
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": "Availability check failed", "reason": str(exc)}), 500

    @api.route("/date-availability", methods=["GET", "POST"])
    def date_availability():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        if request.method == "GET":
            return jsonify(
                {
                    "endpoint": "/api/date-availability",
                    "method": "POST",
                    "example_payload": {"date": "2026-04-20"},
                }
            )

        data = request.json
        auto_mark_no_shows()
        validation_error = validate_payload(data, ["date"])
        if validation_error:
            return jsonify(validation_error), 400

        date = data["date"]

        try:
            cottages = (
                supabase.table("cottages")
                .select("id,name,price,capacity,status")
                .eq("status", "active")
                .execute()
            )

            if not cottages.data:
                cottages = supabase.table("cottages").select("id,name,price,capacity,status").execute()

            if not cottages.data:
                return jsonify(
                    {
                        "available": False,
                        "reason": "No cottages configured",
                        "date": date,
                        "available_cottages": 0,
                        "total_cottages": 0,
                        "available_cottage_ids": [],
                    }
                )

            existing = (
                supabase.table("bookings")
                .select("cottage_id,status")
                .eq("date", date)
                .in_("status", ["confirmed", "checked_in"])
                .execute()
            )

            booked_ids = {
                row["cottage_id"]
                for row in (existing.data or [])
                if is_blocking_status(row.get("status"))
            }
            available_cottages = [row for row in cottages.data if row["id"] not in booked_ids]

            return jsonify(
                {
                    "available": len(available_cottages) > 0,
                    "date": date,
                    "available_cottages": len(available_cottages),
                    "total_cottages": len(cottages.data),
                    "available_cottage_ids": [row["id"] for row in available_cottages],
                    "unavailable_cottage_ids": list(booked_ids),
                }
            )
        except Exception as exc:
            return jsonify({"error": "Date availability check failed", "reason": str(exc)}), 500

    @api.route("/book", methods=["GET", "POST"])
    def create_booking():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        if request.method == "GET":
            return jsonify(
                {
                    "endpoint": "/api/book",
                    "method": "POST",
                    "example_payload": {
                        "cottage_id": "PUT_ID_HERE",
                        "date": "2026-04-20",
                        "arrival_time": "09:00",
                        "adults": 2,
                        "children": 1,
                        "full_name": "Juan Dela Cruz",
                        "email": "juan@example.com",
                        "phone": "+639171234567",
                    },
                }
            )

        data = request.json
        validation_error = validate_payload(
            data,
            ["cottage_id", "date", "arrival_time", "adults", "children", "full_name", "email", "phone"],
        )
        if validation_error:
            return jsonify(validation_error), 400

        cottage_id = data["cottage_id"]
        date = data["date"]
        arrival_time, arrival_error = validate_arrival_time(data.get("arrival_time"))
        if arrival_error:
            return jsonify(arrival_error), 400

        grace_period_minutes, grace_error = parse_grace_period_minutes(data.get("grace_period_minutes"))
        if grace_error:
            return jsonify(grace_error), 400

        full_name, email, phone, contact_error = validate_contact_fields(
            data.get("full_name"),
            data.get("email"),
            data.get("phone"),
        )
        if contact_error:
            return jsonify(contact_error), 400

        user_id = data.get("user_id")
        if user_id:
            user_uuid_error = validate_uuid("user_id", user_id)
            if user_uuid_error:
                return jsonify(user_uuid_error), 400
        else:
            user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))

        uuid_error = validate_uuid("cottage_id", cottage_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        adults, adult_error = parse_non_negative_int("adults", data["adults"])
        if adult_error:
            return jsonify(adult_error), 400

        children, child_error = parse_non_negative_int("children", data["children"])
        if child_error:
            return jsonify(child_error), 400

        num_people = adults + children
        if num_people <= 0:
            return jsonify({"error": "At least one guest is required"}), 400

        try:
            auto_mark_no_shows()
            availability = check_availability_logic(cottage_id, date, num_people)
            if not availability["available"]:
                return jsonify({"error": "This cottage is already booked on the selected date."}), 409

            duplicate = (
                supabase.table("bookings")
                .select("id,status")
                .eq("cottage_id", cottage_id)
                .eq("date", date)
                .in_("status", ["confirmed", "checked_in"])
                .execute()
            )

            has_blocking_duplicate = any(is_blocking_status(row.get("status")) for row in (duplicate.data or []))
            if has_blocking_duplicate:
                return jsonify({"error": "Duplicate booking is not allowed for this cottage and date."}), 409

            cottage = (
                supabase.table("cottages")
                .select("*")
                .eq("id", cottage_id)
                .execute()
            )

            if not cottage.data:
                return jsonify({"error": "Cottage not found"}), 404

            cottage_price = int(cottage.data[0]["price"])
            adult_total = adults * ADULT_FEE
            child_total = children * CHILD_FEE
            entrance_total = adult_total + child_total
            total_amount = cottage_price + entrance_total
            booking_id = str(uuid.uuid4())

            payload = {
                "id": booking_id,
                "user_id": user_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "cottage_id": cottage_id,
                "date": date,
                "arrival_time": arrival_time,
                "grace_period_minutes": grace_period_minutes,
                "checked_in": False,
                "checked_in_at": None,
                "checked_out_at": None,
                "adults": adults,
                "children": children,
                "num_people": num_people,
                "total_amount": total_amount,
                "status": "pending",
            }

            try:
                supabase.table("bookings").insert(payload).execute()
            except Exception as exc:
                error_text = str(exc)
                if (
                    "Could not find the 'full_name' column" in error_text
                    or "Could not find the 'email' column" in error_text
                    or "Could not find the 'phone' column" in error_text
                ):
                    legacy_payload = {
                        "id": booking_id,
                        "user_id": user_id,
                        "cottage_id": cottage_id,
                        "date": date,
                        "adults": adults,
                        "children": children,
                        "num_people": num_people,
                        "total_amount": total_amount,
                        "status": "pending",
                    }
                    supabase.table("bookings").insert(legacy_payload).execute()
                else:
                    raise

            return jsonify(
                {
                    "message": "Booking submitted. Awaiting approval.",
                    "booking_id": booking_id,
                    "arrival_time": arrival_time,
                    "grace_period_minutes": grace_period_minutes,
                    "pricing": {
                        "cottage_price": cottage_price,
                        "adult_fee": ADULT_FEE,
                        "child_fee": CHILD_FEE,
                        "adult_total": adult_total,
                        "child_total": child_total,
                        "entrance_total": entrance_total,
                        "total_amount": total_amount,
                    },
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/cottages", methods=["GET"])
    def get_cottages():
        result = (
            supabase.table("cottages")
            .select("*")
            .eq("status", "active")
            .execute()
        )

        if not result.data:
            result = supabase.table("cottages").select("*").execute()

        return jsonify(result.data)

    @api.route("/health/supabase", methods=["GET"])
    def supabase_health():
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
        )

        if not supabase_url or not supabase_key:
            return (
                jsonify(
                    {
                        "ok": False,
                        "service": "supabase",
                        "reason": "Missing SUPABASE_URL and one of SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY",
                    }
                ),
                500,
            )

        try:
            result = supabase.table("cottages").select("id").limit(1).execute()
            rows = len(result.data or [])

            return jsonify(
                {
                    "ok": True,
                    "service": "supabase",
                    "message": "Connection successful",
                    "table_checked": "cottages",
                    "sample_rows_returned": rows,
                }
            )
        except Exception as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "service": "supabase",
                        "reason": str(exc),
                    }
                ),
                500,
            )

    @api.route("/admin/bookings", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_bookings():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        date_filter = request.args.get("date")
        status_filter = request.args.get("status", "all")
        search_term = request.args.get("search", "")

        try:
            bookings = fetch_admin_bookings(date_filter, status_filter, search_term)
            return jsonify({"bookings": bookings, "count": len(bookings)})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/cottages", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_cottages():
        try:
            auto_mark_no_shows()
            target_date = request.args.get("date") or str(dt_date.today())
            parsed_date = parse_booking_date(target_date)
            if not parsed_date:
                return jsonify({"error": "date must be in YYYY-MM-DD format"}), 400

            result = (
                supabase.table("cottages")
                .select("id,name,capacity,price,status")
                .order("name")
                .execute()
            )

            cottages = result.data or []
            bookings_result = (
                supabase.table("bookings")
                .select("id,cottage_id,status,date")
                .eq("date", target_date)
                .in_("status", ["confirmed", "checked_in"])
                .execute()
            )

            bookings_by_cottage = {}
            priority = {"checked_in": 4, "confirmed": 3, "pending": 2}
            for row in (bookings_result.data or []):
                row_status = canonical_booking_status(row.get("status"))
                if not is_blocking_status(row_status):
                    continue

                cottage_id = row.get("cottage_id")
                existing = bookings_by_cottage.get(cottage_id)
                existing_rank = priority.get((existing or {}).get("status", ""), 0)
                next_rank = priority.get(row_status, 0)
                if not existing or next_rank >= existing_rank:
                    bookings_by_cottage[cottage_id] = {
                        "booking_id": row.get("id"),
                        "status": row_status,
                        "date": row.get("date"),
                    }

            enriched = []
            available_count = 0
            booked_count = 0
            for cottage in cottages:
                day_booking = bookings_by_cottage.get(cottage.get("id"))
                is_available_today = day_booking is None and str(cottage.get("status", "")).lower() == "active"
                if is_available_today:
                    available_count += 1
                elif day_booking:
                    booked_count += 1

                enriched.append(
                    {
                        **cottage,
                        "is_available_today": is_available_today,
                        "day_booking": day_booking,
                    }
                )

            return jsonify(
                {
                    "date": target_date,
                    "available_count": available_count,
                    "booked_count": booked_count,
                    "total_count": len(cottages),
                    "cottages": enriched,
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/cottages", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_create_cottage():
        data = request.json
        validation_error = validate_payload(data, ["name", "capacity", "price"])
        if validation_error:
            return jsonify(validation_error), 400

        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        if len(name) > 120:
            return jsonify({"error": "name is too long"}), 400

        capacity, capacity_error = parse_non_negative_int("capacity", data.get("capacity"))
        if capacity_error:
            return jsonify(capacity_error), 400
        if capacity <= 0:
            return jsonify({"error": "capacity must be greater than 0"}), 400

        price, price_error = parse_non_negative_int("price", data.get("price"))
        if price_error:
            return jsonify(price_error), 400
        if price <= 0:
            return jsonify({"error": "price must be greater than 0"}), 400

        status, status_error = validate_cottage_status(data.get("status", "active"))
        if status_error:
            return jsonify(status_error), 400

        try:
            payload = {
                "id": str(uuid.uuid4()),
                "name": name,
                "capacity": capacity,
                "price": price,
                "status": status,
            }

            insert = (
                supabase.table("cottages")
                .insert(payload)
                .execute()
            )

            created = (insert.data or [payload])[0]
            return jsonify({"message": "Cottage created", "cottage": created}), 201
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/cottages/<cottage_id>", methods=["PUT"])
    @admin_required
    @role_required("admin")
    def admin_update_cottage(cottage_id):
        uuid_error = validate_uuid("cottage_id", cottage_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        data = request.json
        if not data:
            return jsonify({"error": "JSON body is required"}), 400

        updates = {}

        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name is required"}), 400
            if len(name) > 120:
                return jsonify({"error": "name is too long"}), 400
            updates["name"] = name

        if "capacity" in data:
            capacity, capacity_error = parse_non_negative_int("capacity", data.get("capacity"))
            if capacity_error:
                return jsonify(capacity_error), 400
            if capacity <= 0:
                return jsonify({"error": "capacity must be greater than 0"}), 400
            updates["capacity"] = capacity

        if "price" in data:
            price, price_error = parse_non_negative_int("price", data.get("price"))
            if price_error:
                return jsonify(price_error), 400
            if price <= 0:
                return jsonify({"error": "price must be greater than 0"}), 400
            updates["price"] = price

        if "status" in data:
            status, status_error = validate_cottage_status(data.get("status"))
            if status_error:
                return jsonify(status_error), 400
            updates["status"] = status

        if not updates:
            return jsonify({"error": "No valid fields provided for update"}), 400

        try:
            existing = (
                supabase.table("cottages")
                .select("id")
                .eq("id", cottage_id)
                .limit(1)
                .execute()
            )

            if not existing.data:
                return jsonify({"error": "Cottage not found"}), 404

            updated = (
                supabase.table("cottages")
                .update(updates)
                .eq("id", cottage_id)
                .execute()
            )

            payload = (updated.data or [{"id": cottage_id, **updates}])[0]
            return jsonify({"message": "Cottage updated", "cottage": payload})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/cottages/<cottage_id>", methods=["DELETE"])
    @admin_required
    @role_required("admin")
    def admin_delete_cottage(cottage_id):
        uuid_error = validate_uuid("cottage_id", cottage_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            existing = (
                supabase.table("cottages")
                .select("id")
                .eq("id", cottage_id)
                .limit(1)
                .execute()
            )

            if not existing.data:
                return jsonify({"error": "Cottage not found"}), 404

            (
                supabase.table("cottages")
                .delete()
                .eq("id", cottage_id)
                .execute()
            )

            return jsonify({"message": "Cottage deleted", "id": cottage_id})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/dashboard-metrics", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_dashboard_metrics():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        target_date = request.args.get("date") or str(dt_date.today())

        try:
            released = auto_mark_no_shows()
            today_bookings = fetch_admin_bookings(target_date, "all", "")
            pending = sum(1 for b in today_bookings if b.get("status") == "pending")
            confirmed = sum(1 for b in today_bookings if b.get("status") == "confirmed")
            checked_in = sum(1 for b in today_bookings if b.get("status") == "checked_in")
            no_show = sum(1 for b in today_bookings if b.get("status") == "no_show")

            capacity_current = None
            capacity_max = None
            capacity_usage_percent = None

            try:
                capacity_result = (
                    supabase.table("daily_capacity")
                    .select("current_people,max_people")
                    .eq("date", target_date)
                    .limit(1)
                    .execute()
                )

                if capacity_result.data:
                    capacity_current = capacity_result.data[0].get("current_people", 0)
                    capacity_max = capacity_result.data[0].get("max_people", 0)
                    if capacity_max:
                        capacity_usage_percent = round((capacity_current / capacity_max) * 100, 1)
            except Exception:
                capacity_current = None
                capacity_max = None
                capacity_usage_percent = None

            return jsonify(
                {
                    "date": target_date,
                    "total_bookings_today": len(today_bookings),
                    "pending_approvals": pending,
                    "confirmed_bookings": confirmed,
                    "checked_in_bookings": checked_in,
                    "arrived_bookings": checked_in,
                    "no_show_bookings": no_show,
                    "auto_released_no_show": released,
                    "capacity_current": capacity_current,
                    "capacity_max": capacity_max,
                    "capacity_usage_percent": capacity_usage_percent,
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/bookings/<booking_id>/status", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_update_booking_status(booking_id):
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        data = request.json
        validation_error = validate_payload(data, ["status"])
        if validation_error:
            return jsonify(validation_error), 400

        try:
            return update_booking_status(booking_id, data["status"])
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/bookings/<booking_id>/checkout", methods=["POST"])
    @admin_required
    @role_required("admin", "staff")
    def admin_checkout_booking(booking_id):
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            booking = fetch_booking_by_id(booking_id)
            if not booking:
                return jsonify({"error": "Booking not found"}), 404

            current_status = canonical_booking_status(booking.get("status"))
            if current_status == "completed":
                return jsonify({"message": "Booking already checked out", "booking": booking})

            if current_status != "checked_in":
                return jsonify({"error": "Only checked-in bookings can be checked out"}), 400

            (
                supabase.table("bookings")
                .update(
                    {
                        "status": "completed",
                        "checked_out_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("id", booking_id)
                .execute()
            )

            updated = fetch_booking_by_id(booking_id) or {**booking, "status": "completed"}
            return jsonify({"message": "Booking marked as completed", "booking": updated})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/bookings/<booking_id>/approve", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_approve_booking(booking_id):
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        try:
            return approve_booking_workflow(booking_id)
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/bookings/<booking_id>/resend-email", methods=["POST"])
    @admin_required
    @role_required("admin", "staff")
    def admin_resend_booking_email(booking_id):
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            booking = fetch_booking_by_id(booking_id)
            if not booking:
                return jsonify({"error": "Booking not found"}), 404

            status = canonical_booking_status(booking.get("status"))
            if status not in ["confirmed", "checked_in", "completed"]:
                return jsonify({"error": "Booking must be confirmed before sending confirmation email"}), 400

            if not booking.get("email"):
                return jsonify({"error": "Booking has no email address"}), 400

            if status == "confirmed" and not booking.get("qr_code"):
                qr_code_value, _, _ = ensure_booking_qr(booking)
                booking["qr_code"] = qr_code_value

            sent, send_error = send_booking_confirmation_email(booking)
            if not sent:
                return jsonify({"error": "Failed to send confirmation email", "reason": send_error}), 502

            latest = fetch_booking_by_id(booking_id) or booking
            return jsonify({"message": "Confirmation email sent", "booking": latest, "email_sent": True})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/scan", methods=["POST"])
    @admin_required
    @role_required("admin", "staff")
    def scan_booking():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        data = request.json
        auth_error = validate_scanner_authorization(data)
        if auth_error:
            return auth_error

        booking_id, token_date, payload_error = extract_booking_id_from_scan_payload(data)
        if payload_error:
            return jsonify(payload_error), 400

        auto_mark_no_shows()

        admin_id = resolve_admin_id(data)
        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify({"status": "INVALID", **uuid_error}), 400

        try:
            booking = fetch_booking_by_id(booking_id)
            if not booking:
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "INVALID", "message": "Booking not found"}), 404

            status = canonical_booking_status(booking.get("status"))
            if status == "checked_in":
                (
                    supabase.table("bookings")
                    .update(
                        {
                            "status": "completed",
                            "checked_out_at": datetime.utcnow().isoformat(),
                        }
                    )
                    .eq("id", booking_id)
                    .execute()
                )

                write_scan_log(booking_id, "valid", admin_id)
                checked_out_booking = fetch_booking_by_id(booking_id) or {**booking, "status": "completed"}
                return jsonify({"status": "CHECKED_OUT", "message": "Check-out accepted", "booking": checked_out_booking})

            if status == "completed":
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "ALREADY_CHECKED_OUT", "message": "Guest is already checked out", "booking": booking}), 409

            if status == "no_show":
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "NO_SHOW", "message": "Booking expired due to no-show", "booking": booking}), 409

            if status != "confirmed":
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "INVALID", "message": "Booking is not confirmed", "booking": booking}), 400

            booking_date = parse_booking_date(booking.get("date"))
            if not booking_date:
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "INVALID", "message": "Booking date is invalid", "booking": booking}), 400

            if token_date and str(booking.get("date")) != token_date:
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"status": "INVALID", "message": "QR date mismatch", "booking": booking}), 400

            if booking_date != dt_date.today():
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify(
                    {
                        "status": "INVALID",
                        "message": "Booking date does not match today",
                        "booking": booking,
                    }
                ), 400

            now_dt = datetime.now()
            deadline = to_arrival_deadline(
                booking.get("date"),
                booking.get("arrival_time"),
                booking.get("grace_period_minutes"),
            )

            if deadline and now_dt > deadline:
                if AUTO_CANCEL_NO_SHOW:
                    (
                        supabase.table("bookings")
                        .update({"status": "no_show"})
                        .eq("id", booking_id)
                        .eq("status", "confirmed")
                        .execute()
                    )
                    latest = fetch_booking_by_id(booking_id) or {**booking, "status": "no_show"}
                    write_scan_log(booking_id, "invalid", admin_id)
                    return jsonify(
                        {
                            "status": "NO_SHOW",
                            "message": "Booking expired after grace period",
                            "booking": latest,
                        }
                    ), 409

                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify(
                    {
                        "status": "LATE",
                        "message": "Arrival is outside grace period. Use admin override if needed.",
                        "booking": booking,
                    }
                ), 409

            (
                supabase.table("bookings")
                .update(
                    {
                        "status": "checked_in",
                        "checked_in": True,
                        "checked_in_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("id", booking_id)
                .execute()
            )

            write_scan_log(booking_id, "valid", admin_id)
            checked_in_booking = fetch_booking_by_id(booking_id) or booking
            return jsonify({"status": "VALID", "message": "Check-in accepted", "booking": checked_in_booking})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify({"status": "INVALID", **body}), status_code

    @api.route("/admin/scan/force-allow", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_force_allow_entry():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        data = request.json
        auth_error = validate_scanner_authorization(data)
        if auth_error:
            return auth_error

        validation_error = validate_payload(data, ["booking_id"])
        if validation_error:
            return jsonify(validation_error), 400

        booking_id = data.get("booking_id")
        uuid_error = validate_uuid("booking_id", booking_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        admin_id = resolve_admin_id(data)

        try:
            auto_mark_no_shows()
            booking = fetch_booking_by_id(booking_id)
            if not booking:
                write_scan_log(booking_id, "invalid", admin_id)
                return jsonify({"error": "Booking not found"}), 404

            (
                supabase.table("bookings")
                .update(
                    {
                        "status": "checked_in",
                        "checked_in": True,
                        "checked_in_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("id", booking_id)
                .execute()
            )

            write_scan_log(booking_id, "valid", admin_id)
            updated = fetch_booking_by_id(booking_id) or booking
            return jsonify(
                {
                    "status": "VALID",
                    "message": "Force allow entry applied",
                    "booking": updated,
                    "forced": True,
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/scan-logs", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_scan_logs():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        limit_value = request.args.get("limit", "50")
        try:
            limit = max(1, min(200, int(limit_value)))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

        try:
            result = (
                supabase.table("scan_logs")
                .select("id,booking_id,scanned_at,result,admin_id")
                .order("scanned_at", desc=True)
                .limit(limit)
                .execute()
            )

            logs = result.data or []
            return jsonify({"logs": logs, "count": len(logs)})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/no-show/refresh", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_refresh_no_show():
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        try:
            released = auto_mark_no_shows()
            return jsonify({"message": "No-show refresh completed", "released": released})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/bookings/<booking_id>/reject", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_reject_booking(booking_id):
        booking_guard = require_booking_enabled()
        if booking_guard:
            return booking_guard

        try:
            return reject_booking_workflow(booking_id)
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code
