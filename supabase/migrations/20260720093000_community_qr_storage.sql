-- Public bucket with one mutable object. Only the designated administrator may
-- create or replace that object; public reads use Supabase's public asset URL.
insert into storage.buckets (
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values (
  'community-assets',
  'community-assets',
  true,
  2097152,
  array['image/png', 'image/jpeg', 'image/webp']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Community QR admin can view object metadata" on storage.objects;
create policy "Community QR admin can view object metadata"
on storage.objects for select
to authenticated
using (
  bucket_id = 'community-assets'
  and name = 'wechat-group.png'
  and auth.jwt() ->> 'email' = 'yuzxfred@gmail.com'
);

drop policy if exists "Community QR admin can upload object" on storage.objects;
create policy "Community QR admin can upload object"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'community-assets'
  and name = 'wechat-group.png'
  and auth.jwt() ->> 'email' = 'yuzxfred@gmail.com'
);

drop policy if exists "Community QR admin can replace object" on storage.objects;
create policy "Community QR admin can replace object"
on storage.objects for update
to authenticated
using (
  bucket_id = 'community-assets'
  and name = 'wechat-group.png'
  and auth.jwt() ->> 'email' = 'yuzxfred@gmail.com'
)
with check (
  bucket_id = 'community-assets'
  and name = 'wechat-group.png'
  and auth.jwt() ->> 'email' = 'yuzxfred@gmail.com'
);
