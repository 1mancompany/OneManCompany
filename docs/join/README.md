# WeChat live QR code

The permanent public entry point is:

`https://1mancompany.github.io/OneManCompany/join/`

`entry-qr.png` encodes that URL and should not be replaced. It is the QR code
shown in the repository README and can also be reused in posters or other
promotional material.

To update an expired or full WeChat group, open
`https://1mancompany.github.io/OneManCompany/join/admin/`, sign in with the
designated administrator account, choose the new QR image, and upload it. The
image is stored as `community-assets/wechat-group.png` in the `onemancompany`
Supabase project. No Git commit or deployment is needed.

The administrator password is stored in the macOS Keychain under
`supabase-onemancompany-admin`. Never add it or a Supabase secret/service-role
key to this repository.
