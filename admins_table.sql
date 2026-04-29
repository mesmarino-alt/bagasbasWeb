create extension if not exists pgcrypto;

create table if not exists admins (
    id uuid primary key default gen_random_uuid(),
    email text unique not null,
    password_hash text not null,
    role text not null default 'admin',
    created_at timestamp without time zone default current_timestamp,
    constraint admins_role_check check (role in ('admin', 'staff'))
);

create index if not exists admins_email_idx on admins (email);
