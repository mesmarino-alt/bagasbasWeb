create extension if not exists pgcrypto;

create table if not exists events (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    description text not null default '',
    image_url text not null default '',
    event_date date,
    location text not null default '',
    tags text[] not null default '{}',
    is_featured boolean not null default false,
    is_published boolean not null default false,
    created_at timestamp with time zone not null default now()
);

alter table if exists events
    add column if not exists title text,
    add column if not exists description text,
    add column if not exists image_url text,
    add column if not exists event_date date,
    add column if not exists location text,
    add column if not exists tags text[],
    add column if not exists is_featured boolean,
    add column if not exists is_published boolean,
    add column if not exists created_at timestamp with time zone;

update events
set
    title = coalesce(title, 'Untitled Event'),
    description = coalesce(description, ''),
    image_url = coalesce(image_url, ''),
    location = coalesce(location, ''),
    tags = coalesce(tags, '{}'::text[]),
    is_featured = coalesce(is_featured, false),
    is_published = coalesce(is_published, false),
    created_at = coalesce(created_at, now());

alter table if exists events
    alter column title set default 'Untitled Event',
    alter column description set default '',
    alter column image_url set default '',
    alter column location set default '',
    alter column tags set default '{}'::text[],
    alter column is_featured set default false,
    alter column is_published set default false,
    alter column created_at set default now();

alter table if exists events
    alter column title set not null,
    alter column description set not null,
    alter column image_url set not null,
    alter column location set not null,
    alter column tags set not null,
    alter column is_featured set not null,
    alter column is_published set not null,
    alter column created_at set not null;

create table if not exists gallery (
    id uuid primary key default gen_random_uuid(),
    image_url text not null default '',
    caption text not null default '',
    category text not null default 'General',
    is_published boolean not null default false,
    created_at timestamp with time zone not null default now()
);

alter table if exists gallery
    add column if not exists image_url text,
    add column if not exists caption text,
    add column if not exists category text,
    add column if not exists is_published boolean,
    add column if not exists created_at timestamp with time zone;

update gallery
set
    image_url = coalesce(image_url, ''),
    caption = coalesce(caption, ''),
    category = coalesce(category, 'General'),
    is_published = coalesce(is_published, false),
    created_at = coalesce(created_at, now());

alter table if exists gallery
    alter column image_url set default '',
    alter column caption set default '',
    alter column category set default 'General',
    alter column is_published set default false,
    alter column created_at set default now();

alter table if exists gallery
    alter column image_url set not null,
    alter column caption set not null,
    alter column category set not null,
    alter column is_published set not null,
    alter column created_at set not null;

create index if not exists idx_events_published_date on events (is_published, event_date);
create index if not exists idx_events_featured on events (is_featured);
create index if not exists idx_gallery_published_created on gallery (is_published, created_at desc);
create index if not exists idx_gallery_category on gallery (category);

-- Storage bucket used by CMS image uploads (events + gallery).
insert into storage.buckets (id, name, public)
values ('cms-media', 'cms-media', true)
on conflict (id) do update set public = excluded.public;

-- Optional RLS examples for public reads. Uncomment if needed for your Supabase project.
-- create policy "Public read cms-media" on storage.objects
--     for select using (bucket_id = 'cms-media');

create table if not exists settings (
    id integer primary key default 1,
    booking_enabled boolean not null default false,
    updated_at timestamp with time zone not null default now()
);

alter table if exists settings
    add column if not exists booking_enabled boolean,
    add column if not exists updated_at timestamp with time zone;

update settings
set
    booking_enabled = coalesce(booking_enabled, false),
    updated_at = coalesce(updated_at, now());

alter table if exists settings
    alter column booking_enabled set default false,
    alter column updated_at set default now();

alter table if exists settings
    alter column booking_enabled set not null,
    alter column updated_at set not null;

insert into settings (id, booking_enabled)
values (1, false)
on conflict (id) do nothing;

create table if not exists inquiries (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    email text not null,
    phone text not null,
    preferred_date date,
    message text not null,
    status text not null default 'new',
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now()
);

alter table if exists inquiries
    add column if not exists name text,
    add column if not exists email text,
    add column if not exists phone text,
    add column if not exists preferred_date date,
    add column if not exists message text,
    add column if not exists status text,
    add column if not exists created_at timestamp with time zone,
    add column if not exists updated_at timestamp with time zone;

update inquiries
set
    name = coalesce(name, 'Unknown Visitor'),
    email = coalesce(email, 'unknown@example.com'),
    phone = coalesce(phone, 'N/A'),
    message = coalesce(message, ''),
    status = case
        when status in ('new', 'contacted', 'archived') then status
        else 'new'
    end,
    created_at = coalesce(created_at, now()),
    updated_at = coalesce(updated_at, now());

alter table if exists inquiries
    alter column name set default 'Unknown Visitor',
    alter column email set default 'unknown@example.com',
    alter column phone set default 'N/A',
    alter column message set default '',
    alter column status set default 'new',
    alter column created_at set default now(),
    alter column updated_at set default now();

alter table if exists inquiries
    alter column name set not null,
    alter column email set not null,
    alter column phone set not null,
    alter column message set not null,
    alter column status set not null,
    alter column created_at set not null,
    alter column updated_at set not null;

create index if not exists idx_inquiries_status_created on inquiries (status, created_at desc);
