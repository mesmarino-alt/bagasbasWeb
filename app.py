from flask import Flask, render_template
from supabase import create_client
from dotenv import load_dotenv
import os
from api_routes import create_api_blueprint
from web_routes.bookings import register_booking_routes
from web_routes.cms import register_cms_routes

load_dotenv()

app = Flask(__name__)

debug_enabled = os.getenv("FLASK_DEBUG", "1") == "1"

supabase_key = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    supabase_key
)

app.register_blueprint(create_api_blueprint(supabase))


def is_booking_enabled():
    default_value = os.getenv("BOOKING_ENABLED_DEFAULT", "0").strip().lower() in {"1", "true", "yes", "on"}
    try:
        result = (
            supabase.table("settings")
            .select("booking_enabled")
            .order("id")
            .limit(1)
            .execute()
        )
        if result.data:
            return bool(result.data[0].get("booking_enabled"))
    except Exception:
        return default_value

    return default_value


def auth_template_context():
    return {
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
    }

@app.route('/')
def index():
    return render_template('index.html', booking_enabled=is_booking_enabled())


register_booking_routes(app, is_booking_enabled, auth_template_context)
register_cms_routes(app, is_booking_enabled, auth_template_context)



if __name__ == '__main__':
    app.run(
        debug=debug_enabled,
        host=os.getenv('HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', '5000'))
    )
