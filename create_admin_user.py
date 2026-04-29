import argparse
import os
import uuid

from dotenv import load_dotenv
from supabase import create_client
from werkzeug.security import generate_password_hash


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Create a hashed admin/staff account in Supabase admins table")
    parser.add_argument("--email", required=True, help="Admin/staff email")
    parser.add_argument("--password", required=True, help="Plain password to hash")
    parser.add_argument("--role", default="admin", choices=["admin", "staff"], help="Account role")
    parser.add_argument(
        "--if-exists",
        default="error",
        choices=["error", "update", "skip"],
        help="Behavior when email already exists",
    )
    args = parser.parse_args()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = (
        os.getenv("SUPABASE_SECRET_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
    )

    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing SUPABASE_URL and one of SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY")

    client = create_client(supabase_url, supabase_key)
    email = args.email.strip().lower()
    password_hash = generate_password_hash(args.password)

    existing = (
        client.table("admins")
        .select("id,email,role")
        .eq("email", email)
        .limit(1)
        .execute()
    )

    if existing.data:
        row = existing.data[0]
        if args.if_exists == "skip":
            print(f"Admin already exists: {row.get('email')} ({row.get('role')})")
            return

        if args.if_exists == "update":
            updated = (
                client.table("admins")
                .update({"password_hash": password_hash, "role": args.role})
                .eq("id", row["id"])
                .execute()
            )
            out = (updated.data or [row])[0]
            print(f"Updated admin user: {out.get('email')} ({out.get('role')})")
            return

        raise RuntimeError(
            "Admin email already exists. Use --if-exists update to reset password/role, "
            "or --if-exists skip to leave it unchanged."
        )

    payload = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": password_hash,
        "role": args.role,
    }

    result = client.table("admins").insert(payload).execute()
    row = (result.data or [payload])[0]
    print(f"Created admin user: {row.get('email')} ({row.get('role')})")


if __name__ == "__main__":
    main()
