create table if not exists scan_logs (
    id uuid primary key,
    booking_id uuid,
    scanned_at timestamp default now(),
    result text,
    admin_id text
);

create index if not exists idx_scan_logs_booking_id on scan_logs (booking_id);
create index if not exists idx_scan_logs_scanned_at on scan_logs (scanned_at desc);
