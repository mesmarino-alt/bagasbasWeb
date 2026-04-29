from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import uuid

from flask import jsonify, request


def register_cms_api_routes(api, ctx):
    supabase = ctx["supabase"]
    admin_required = ctx["admin_required"]
    role_required = ctx["role_required"]
    map_db_error = ctx["map_db_error"]
    get_settings_record = ctx["get_settings_record"]
    parse_bool = ctx["parse_bool"]
    validate_inquiry_payload = ctx["validate_inquiry_payload"]
    parse_required_bool = ctx["parse_required_bool"]
    validate_uuid = ctx["validate_uuid"]
    validate_event_payload = ctx["validate_event_payload"]
    validate_gallery_payload = ctx["validate_gallery_payload"]
    CMS_STORAGE_BUCKET = ctx["CMS_STORAGE_BUCKET"]
    MAX_CMS_IMAGE_BYTES = ctx["MAX_CMS_IMAGE_BYTES"]
    ALLOWED_CMS_IMAGE_MIME = ctx["ALLOWED_CMS_IMAGE_MIME"]

    @api.route("/events", methods=["GET"])
    def get_events():
        try:
            result = (
                supabase.table("events")
                .select("*")
                .eq("is_published", True)
                .order("event_date")
                .order("created_at")
                .execute()
            )
            return jsonify(result.data or [])
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/gallery", methods=["GET"])
    def get_gallery():
        category = (request.args.get("category") or "").strip()
        try:
            query = (
                supabase.table("gallery")
                .select("*")
                .eq("is_published", True)
                .order("created_at", desc=True)
            )
            if category:
                query = query.eq("category", category)

            result = query.execute()
            return jsonify(result.data or [])
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/settings", methods=["GET"])
    def get_public_settings():
        settings_record = get_settings_record() or {}
        return jsonify(
            {
                "booking_enabled": bool(settings_record.get("booking_enabled", parse_bool(os.getenv("BOOKING_ENABLED_DEFAULT", "0"), default=False))),
                "updated_at": settings_record.get("updated_at"),
            }
        )

    @api.route("/inquiries", methods=["POST"])
    def create_inquiry():
        payload, payload_error = validate_inquiry_payload(request.json)
        if payload_error:
            return jsonify(payload_error), 400

        payload["id"] = str(uuid.uuid4())
        payload["created_at"] = datetime.utcnow().isoformat()
        payload["updated_at"] = payload["created_at"]

        try:
            legacy_payload = {
                "id": payload["id"],
                "full_name": payload.get("name"),
                "email": payload.get("email"),
                "phone": payload.get("phone"),
                "message": payload.get("message"),
                "status": payload.get("status", "new"),
                "created_at": payload.get("created_at"),
            }
            hybrid_payload = {**payload, "full_name": payload.get("name")}
            legacy_hybrid_payload = {**legacy_payload, "name": payload.get("name")}

            payload_variants = [payload, hybrid_payload, legacy_payload, legacy_hybrid_payload]
            result = None
            last_exc = None

            for candidate in payload_variants:
                try:
                    result = supabase.table("inquiries").insert(candidate).execute()
                    break
                except Exception as exc:
                    last_exc = exc
                    error_text = str(exc)
                    schema_compat_error = (
                        "Could not find the 'name' column" in error_text
                        or "Could not find the 'full_name' column" in error_text
                        or "Could not find the 'preferred_date' column" in error_text
                        or "Could not find the 'updated_at' column" in error_text
                        or "null value in column \"full_name\"" in error_text
                        or "null value in column \"name\"" in error_text
                    )
                    if not schema_compat_error:
                        raise

            if result is None and last_exc is not None:
                raise last_exc

            created = (result.data or [payload])[0]
            if isinstance(created, dict):
                created["name"] = created.get("name") or created.get("full_name") or payload.get("name")
            return jsonify({"message": "Inquiry submitted", "inquiry": created}), 201
        except Exception as exc:
            print("create_inquiry failed:", repr(exc))
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/settings", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_settings():
        settings_record = get_settings_record() or {}
        booking_enabled = bool(
            settings_record.get("booking_enabled", parse_bool(os.getenv("BOOKING_ENABLED_DEFAULT", "0"), default=False))
        )
        return jsonify(
            {
                "settings": {
                    "booking_enabled": booking_enabled,
                    "mode": "booking" if booking_enabled else "cms",
                    "updated_at": settings_record.get("updated_at"),
                }
            }
        )

    @api.route("/admin/settings", methods=["PUT"])
    @admin_required
    @role_required("admin")
    def admin_update_settings():
        if not isinstance(request.json, dict):
            return jsonify({"error": "JSON body is required"}), 400

        if "booking_enabled" not in request.json:
            return jsonify({"error": "booking_enabled is required"}), 400

        booking_enabled, bool_error = parse_required_bool("booking_enabled", request.json.get("booking_enabled"))
        if bool_error:
            return jsonify(bool_error), 400

        now_iso = datetime.utcnow().isoformat()
        payload = {
            "booking_enabled": booking_enabled,
            "updated_at": now_iso,
        }

        try:
            existing = (
                supabase.table("settings")
                .select("id")
                .eq("id", 1)
                .limit(1)
                .execute()
            )

            if existing.data:
                try:
                    result = (
                        supabase.table("settings")
                        .update(payload)
                        .eq("id", 1)
                        .execute()
                    )
                except Exception as exc:
                    error_text = str(exc)
                    if "Could not find the 'updated_at' column" in error_text:
                        result = (
                            supabase.table("settings")
                            .update({"booking_enabled": booking_enabled})
                            .eq("id", 1)
                            .execute()
                        )
                    else:
                        raise
            else:
                try:
                    result = supabase.table("settings").insert({"id": 1, **payload}).execute()
                except Exception as exc:
                    error_text = str(exc)
                    if "Could not find the 'updated_at' column" in error_text:
                        result = supabase.table("settings").insert({"id": 1, "booking_enabled": booking_enabled}).execute()
                    else:
                        raise

            updated = (result.data or [{"id": 1, **payload}])[0]
            enabled = bool(updated.get("booking_enabled", booking_enabled))
            return jsonify(
                {
                    "message": "Settings updated",
                    "settings": {
                        "booking_enabled": enabled,
                        "mode": "booking" if enabled else "cms",
                        "updated_at": updated.get("updated_at", now_iso),
                    },
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/inquiries", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_inquiries():
        status_filter = str(request.args.get("status") or "all").strip().lower()
        try:
            query = supabase.table("inquiries").select("*").order("created_at", desc=True)
            if status_filter in {"new", "contacted", "archived"}:
                query = query.eq("status", status_filter)

            result = query.execute()
            inquiries = result.data or []
            return jsonify({"inquiries": inquiries, "count": len(inquiries)})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/inquiries/<inquiry_id>", methods=["PUT"])
    @admin_required
    @role_required("admin", "staff")
    def admin_update_inquiry(inquiry_id):
        uuid_error = validate_uuid("inquiry_id", inquiry_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        if not isinstance(request.json, dict):
            return jsonify({"error": "JSON body is required"}), 400

        status = str(request.json.get("status") or "").strip().lower()
        if status not in {"new", "contacted", "archived"}:
            return jsonify({"error": "status must be one of: new, contacted, archived"}), 400

        try:
            existing = (
                supabase.table("inquiries")
                .select("id")
                .eq("id", inquiry_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                return jsonify({"error": "Inquiry not found"}), 404

            result = (
                supabase.table("inquiries")
                .update({"status": status, "updated_at": datetime.utcnow().isoformat()})
                .eq("id", inquiry_id)
                .execute()
            )

            updated = (result.data or [{"id": inquiry_id, "status": status}])[0]
            return jsonify({"message": "Inquiry updated", "inquiry": updated})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/events", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_events():
        include_unpublished = parse_bool(request.args.get("include_unpublished"), default=True)
        try:
            query = supabase.table("events").select("*")
            if not include_unpublished:
                query = query.eq("is_published", True)

            result = query.order("event_date").order("created_at", desc=True).execute()
            return jsonify({"events": result.data or [], "count": len(result.data or [])})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/events", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_create_event():
        payload, payload_error = validate_event_payload(request.json, partial=False)
        if payload_error:
            return jsonify(payload_error), 400

        payload["id"] = str(uuid.uuid4())

        try:
            result = supabase.table("events").insert(payload).execute()
            created = (result.data or [payload])[0]
            return jsonify({"message": "Event created", "event": created}), 201
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/events/<event_id>", methods=["PUT"])
    @admin_required
    @role_required("admin")
    def admin_update_event(event_id):
        uuid_error = validate_uuid("event_id", event_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        payload, payload_error = validate_event_payload(request.json, partial=True)
        if payload_error:
            return jsonify(payload_error), 400

        try:
            existing = (
                supabase.table("events")
                .select("id")
                .eq("id", event_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                return jsonify({"error": "Event not found"}), 404

            result = (
                supabase.table("events")
                .update(payload)
                .eq("id", event_id)
                .execute()
            )
            updated = (result.data or [{"id": event_id, **payload}])[0]
            return jsonify({"message": "Event updated", "event": updated})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/events/<event_id>", methods=["DELETE"])
    @admin_required
    @role_required("admin")
    def admin_delete_event(event_id):
        uuid_error = validate_uuid("event_id", event_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            existing = (
                supabase.table("events")
                .select("id")
                .eq("id", event_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                return jsonify({"error": "Event not found"}), 404

            supabase.table("events").delete().eq("id", event_id).execute()
            return jsonify({"message": "Event deleted", "id": event_id})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/gallery", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_get_gallery():
        include_unpublished = parse_bool(request.args.get("include_unpublished"), default=True)
        category = (request.args.get("category") or "").strip()
        try:
            query = supabase.table("gallery").select("*")
            if not include_unpublished:
                query = query.eq("is_published", True)
            if category:
                query = query.eq("category", category)

            result = query.order("created_at", desc=True).execute()
            return jsonify({"gallery": result.data or [], "count": len(result.data or [])})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/gallery", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_create_gallery_item():
        payload, payload_error = validate_gallery_payload(request.json, partial=False)
        if payload_error:
            return jsonify(payload_error), 400

        payload["id"] = str(uuid.uuid4())

        try:
            result = supabase.table("gallery").insert(payload).execute()
            created = (result.data or [payload])[0]
            return jsonify({"message": "Gallery item created", "item": created}), 201
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/gallery/<item_id>", methods=["PUT"])
    @admin_required
    @role_required("admin")
    def admin_update_gallery_item(item_id):
        uuid_error = validate_uuid("item_id", item_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        payload, payload_error = validate_gallery_payload(request.json, partial=True)
        if payload_error:
            return jsonify(payload_error), 400

        try:
            existing = (
                supabase.table("gallery")
                .select("id")
                .eq("id", item_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                return jsonify({"error": "Gallery item not found"}), 404

            result = (
                supabase.table("gallery")
                .update(payload)
                .eq("id", item_id)
                .execute()
            )
            updated = (result.data or [{"id": item_id, **payload}])[0]
            return jsonify({"message": "Gallery item updated", "item": updated})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/gallery/<item_id>", methods=["DELETE"])
    @admin_required
    @role_required("admin")
    def admin_delete_gallery_item(item_id):
        uuid_error = validate_uuid("item_id", item_id)
        if uuid_error:
            return jsonify(uuid_error), 400

        try:
            existing = (
                supabase.table("gallery")
                .select("id")
                .eq("id", item_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                return jsonify({"error": "Gallery item not found"}), 404

            supabase.table("gallery").delete().eq("id", item_id).execute()
            return jsonify({"message": "Gallery item deleted", "id": item_id})
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code

    @api.route("/admin/upload-image", methods=["POST"])
    @admin_required
    @role_required("admin")
    def admin_upload_image():
        uploaded = request.files.get("file")
        if not uploaded:
            return jsonify({"error": "file is required (multipart/form-data)"}), 400

        file_name = str(uploaded.filename or "").strip()
        if not file_name:
            return jsonify({"error": "file must have a filename"}), 400

        module = str(request.form.get("module") or "general").strip().lower()
        if module not in {"events", "gallery", "general"}:
            module = "general"

        content_type = str(uploaded.mimetype or "").strip().lower()
        extension = ALLOWED_CMS_IMAGE_MIME.get(content_type)
        if not extension:
            suffix = Path(file_name).suffix.lower().replace(".", "")
            if suffix in {"jpg", "jpeg"}:
                extension = "jpg"
                content_type = "image/jpeg"
            elif suffix in {"png", "webp", "gif"}:
                extension = suffix
                content_type = f"image/{suffix}"

        if not extension:
            return jsonify({"error": "Unsupported image type. Use JPEG, PNG, WEBP, or GIF."}), 400

        file_bytes = uploaded.read()
        if not file_bytes:
            return jsonify({"error": "Uploaded file is empty"}), 400

        if len(file_bytes) > MAX_CMS_IMAGE_BYTES:
            max_mb = round(MAX_CMS_IMAGE_BYTES / (1024 * 1024), 1)
            return jsonify({"error": f"File exceeds {max_mb}MB limit"}), 400

        key = datetime.utcnow().strftime("%Y/%m")
        storage_path = f"{module}/{key}/{uuid.uuid4()}.{extension}"

        try:
            storage = supabase.storage.from_(CMS_STORAGE_BUCKET)
            upload_result = storage.upload(
                path=storage_path,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": "false"},
            )

            public_url = storage.get_public_url(storage_path)
            if isinstance(public_url, str):
                resolved_url = public_url
            else:
                resolved_url = ""

            if isinstance(public_url, dict):
                resolved_url = (
                    public_url.get("publicURL")
                    or public_url.get("publicUrl")
                    or public_url.get("signedURL")
                    or ((public_url.get("data") or {}).get("publicUrl") if isinstance(public_url.get("data"), dict) else "")
                    or ""
                )

            if not resolved_url:
                supabase_url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
                if supabase_url:
                    resolved_url = f"{supabase_url}/storage/v1/object/public/{CMS_STORAGE_BUCKET}/{storage_path}"

            return jsonify(
                {
                    "message": "Image uploaded",
                    "bucket": CMS_STORAGE_BUCKET,
                    "path": storage_path,
                    "public_url": resolved_url,
                    "upload_result": upload_result,
                }
            )
        except Exception as exc:
            return jsonify({"error": "Failed to upload image", "reason": str(exc)}), 500

    @api.route("/admin/cms-metrics", methods=["GET"])
    @admin_required
    @role_required("admin", "staff")
    def admin_cms_metrics():
        def normalize_created_at(value):
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            try:
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except Exception:
                return None

        def safe_recent(created_at_value, threshold):
            parsed = normalize_created_at(created_at_value)
            if not parsed:
                return False
            return parsed >= threshold

        now_utc = datetime.utcnow()
        week_threshold = now_utc - timedelta(days=7)
        today_threshold = datetime.combine(dt_date.today(), datetime.min.time())

        try:
            events_rows = (
                supabase.table("events")
                .select("id,title,is_published,created_at")
                .execute()
            ).data or []

            gallery_rows = (
                supabase.table("gallery")
                .select("id,caption,category,is_published,created_at")
                .execute()
            ).data or []

            inquiries_rows = (
                supabase.table("inquiries")
                .select("id,name,email,message,status,created_at")
                .execute()
            ).data or []

            total_events = sum(1 for row in events_rows if bool(row.get("is_published")))
            total_gallery = sum(1 for row in gallery_rows if bool(row.get("is_published")))
            total_inquiries = len(inquiries_rows)

            draft_events = len(events_rows) - total_events
            draft_gallery = len(gallery_rows) - total_gallery
            published_content = total_events + total_gallery
            draft_content = draft_events + draft_gallery

            new_inquiries_today = sum(1 for row in inquiries_rows if safe_recent(row.get("created_at"), today_threshold))
            new_inquiries_week = sum(1 for row in inquiries_rows if safe_recent(row.get("created_at"), week_threshold))
            recent_events = sum(1 for row in events_rows if safe_recent(row.get("created_at"), week_threshold))
            recent_gallery = sum(1 for row in gallery_rows if safe_recent(row.get("created_at"), week_threshold))

            contacted_count = sum(1 for row in inquiries_rows if str(row.get("status") or "").strip().lower() == "contacted")
            response_rate = round((contacted_count / total_inquiries) * 100, 1) if total_inquiries else 0.0

            activity = []
            for row in events_rows:
                created_at = row.get("created_at")
                activity.append(
                    {
                        "type": "event_created",
                        "label": str(row.get("title") or "Untitled event").strip() or "Untitled event",
                        "details": "Event created",
                        "created_at": created_at,
                    }
                )

            for row in gallery_rows:
                created_at = row.get("created_at")
                caption = str(row.get("caption") or "").strip()
                category = str(row.get("category") or "General").strip() or "General"
                activity.append(
                    {
                        "type": "gallery_uploaded",
                        "label": caption or "Gallery item",
                        "details": f"Gallery upload • {category}",
                        "created_at": created_at,
                    }
                )

            for row in inquiries_rows:
                created_at = row.get("created_at")
                inq_name = str(row.get("name") or row.get("email") or "Visitor").strip() or "Visitor"
                status = str(row.get("status") or "new").strip().lower() or "new"
                activity.append(
                    {
                        "type": "inquiry_received",
                        "label": inq_name,
                        "details": f"Inquiry received • {status}",
                        "created_at": created_at,
                    }
                )

            activity.sort(
                key=lambda item: normalize_created_at(item.get("created_at")) or datetime.min,
                reverse=True,
            )

            return jsonify(
                {
                    "total_events": total_events,
                    "total_gallery": total_gallery,
                    "total_inquiries": total_inquiries,
                    "new_inquiries": new_inquiries_today,
                    "new_inquiries_today": new_inquiries_today,
                    "new_inquiries_week": new_inquiries_week,
                    "response_rate": response_rate,
                    "recent_events": recent_events,
                    "recent_gallery": recent_gallery,
                    "content_updated_week": recent_events + recent_gallery,
                    "published_content": published_content,
                    "draft_content": draft_content,
                    "draft_events": draft_events,
                    "draft_gallery": draft_gallery,
                    "recent_activity": activity[:8],
                }
            )
        except Exception as exc:
            status_code, body = map_db_error(exc)
            return jsonify(body), status_code
