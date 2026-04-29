from flask import redirect, render_template, url_for
import os


def register_cms_routes(app, is_booking_enabled, auth_template_context):
    @app.route("/contact")
    def contact():
        if is_booking_enabled():
            return redirect(url_for("book"))
        return redirect(url_for("index", inquiry="1"))

    @app.route("/admin/events")
    def admin_events_page():
        booking_enabled = is_booking_enabled()
        return render_template(
            "admin_bookings.html",
            booking_enabled=booking_enabled,
            scanner_api_key=os.getenv("SCANNER_API_KEY", "bagasbas-scanner-key"),
            scanner_admin_id=os.getenv("SCANNER_ADMIN_ID", "scanner-device"),
            **auth_template_context(),
        )

    @app.route("/admin/gallery")
    def admin_gallery_page():
        booking_enabled = is_booking_enabled()
        return render_template(
            "admin_bookings.html",
            booking_enabled=booking_enabled,
            scanner_api_key=os.getenv("SCANNER_API_KEY", "bagasbas-scanner-key"),
            scanner_admin_id=os.getenv("SCANNER_ADMIN_ID", "scanner-device"),
            **auth_template_context(),
        )

    @app.route("/admin/login", methods=["GET"])
    def admin_login_page():
        return render_template("admin_login.html", **auth_template_context())
