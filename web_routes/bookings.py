from flask import redirect, render_template, url_for
import os


def register_booking_routes(app, is_booking_enabled, auth_template_context):
    @app.route("/book")
    def book():
        if not is_booking_enabled():
            return redirect(url_for("contact"))
        return render_template("book.html")

    @app.route("/book.html")
    def book_html():
        if not is_booking_enabled():
            return redirect(url_for("contact"))
        return render_template("book.html")

    @app.route("/admin")
    @app.route("/admin/dashboard")
    def admin_page():
        booking_enabled = is_booking_enabled()
        return render_template(
            "admin_bookings.html",
            booking_enabled=booking_enabled,
            scanner_api_key=os.getenv("SCANNER_API_KEY", "bagasbas-scanner-key"),
            scanner_admin_id=os.getenv("SCANNER_ADMIN_ID", "scanner-device"),
            **auth_template_context(),
        )

    @app.route("/admin/scanner")
    def admin_scanner_page():
        booking_enabled = is_booking_enabled()
        if not booking_enabled:
            return redirect(url_for("admin_page", _anchor="events"))

        return render_template(
            "admin_scanner.html",
            booking_enabled=booking_enabled,
            scanner_api_key=os.getenv("SCANNER_API_KEY", "bagasbas-scanner-key"),
            scanner_admin_id=os.getenv("SCANNER_ADMIN_ID", "scanner-device"),
            **auth_template_context(),
        )

    @app.route("/receipt")
    def receipt():
        return render_template("receipt.html")
