-- Migration: arrival-time and no-show controls for bookings

alter table if exists bookings
    add column if not exists arrival_time time,
    add column if not exists grace_period_minutes integer default 30,
    add column if not exists checked_in boolean default false,
    add column if not exists checked_in_at timestamp,
    add column if not exists checked_out_at timestamp;

-- Backfill old rows for compatibility
update bookings
set grace_period_minutes = 30
where grace_period_minutes is null;

update bookings
set checked_in = false
where checked_in is null;

-- Normalize legacy statuses to the current lifecycle.
update bookings
set checked_in = true,
    status = 'checked_in'
where status = 'arrived';

update bookings
set status = 'completed',
    checked_in = true,
    checked_out_at = coalesce(checked_out_at, checked_in_at, now())
where status = 'used';

-- Basic constraints for data quality
alter table if exists bookings
    add constraint bookings_grace_period_range
    check (grace_period_minutes is null or (grace_period_minutes >= 0 and grace_period_minutes <= 240));

-- Optional index to speed up no-show sweep
create index if not exists idx_bookings_no_show_sweep
    on bookings (status, date, arrival_time);
