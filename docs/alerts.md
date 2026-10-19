# Alert Telegram dan Discord

Alert dikirim otomatis dari `research --ai`, termasuk riset yang dipanggil oleh
`agent`/`run`, bila proposal LONG/SHORT lolos validasi AI. WAIT, data error dan
proposal kedaluwarsa tidak dikirim. Fitur ini terpisah dari izin eksekusi: pesan
tidak menampilkan label slot penuh atau status eksekusi. Pemeriksaan akun masih
dapat menolak entry; alert tidak berarti order sudah terisi.

Isi pesan: pair/arah, entry dan SL referensi, TP1/TP2/TP3 pada 1R/2R/3R, batas risiko,
alasan AI, invalidasi, nama sumber headline, dan waktu kedaluwarsa WIB. Tidak ada
confidence/peluang menang yang dibuat-buat. Target dihitung dari snapshot; target
posisi aktual dihitung dari harga limit entry; SL lanjutan memakai fill aktual. Alert bukan konfirmasi fill atau exit.

Riwayat hasil setiap sinyal tersedia melalui `python3 main.py signals`.
Lihat [model pelacakan](discovery-and-signals.md); hasil referensi tidak sama dengan
PnL order. Tracker tidak menambahkan label atau mengedit card channel.

## Konfigurasi

Isi di `.env` lokal (jangan kirim token/webhook ke chat atau commit ke Git):

```dotenv
ALERTS_ENABLED=true
ALERT_CHANNELS=discord,telegram
DISCORD_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Untuk satu tujuan saja, gunakan `ALERT_CHANNELS=discord` atau `telegram`.

**Discord:** buat incoming webhook untuk text channel tujuan melalui pengaturan
server/channel → Integrations → Webhooks. Salin URL HTTPS `discord.com/api/webhooks/...`
ke `DISCORD_WEBHOOK_URL`. Forum/thread-specific webhook belum didukung.

**Telegram:** buat bot melalui BotFather, isi tokennya, lalu tambahkan bot sebagai
administrator channel dengan izin posting. Isi `TELEGRAM_CHAT_ID` dengan username
channel publik, misalnya `@nama_channel`, atau numeric chat ID untuk channel privat.
Bot ini hanya mengirim alert; tidak menerima perintah trading dari Telegram.

## Preview dan uji koneksi

```sh
# Contoh sintetis lokal, tidak mengirim pesan
python3 main.py alerts --preview

# Mengirim pesan berlabel TEST ke semua tujuan yang diaktifkan
python3 main.py alerts --test

# Setelah koneksi berhasil, jalankan bot seperti biasa
python3 main.py run
```

Preview juga disimpan ke `runtime/alerts-preview.txt`. Kunci alert tidak dibagikan
ke AI atau OKX. `.env` tetap diabaikan Git.

## Pengiriman dan batasannya

Status pengiriman tersimpan di `runtime/alerts.sqlite3` dan ringkasannya dalam
`runtime/research-output.json`. Ledger tidak menyimpan token atau URL webhook.
Dedup persisten berlaku per tujuan, pair, arah, dan bucket waktu 15 menit dari
paket riset. Bucket baru dapat menghasilkan alert baru untuk arah yang sama.

Request dibatasi 10 detik per tujuan, tidak mengikuti redirect, dan tidak mengirim
mention Discord. Telegram memakai HTML dengan teks AI yang di-escape. Branding
**Powered by [𝕏 web3rai](https://www.x.com/web3rai)** dipasang di bawah isi embed
Discord dan baris terakhir Telegram sebagai tautan bertopeng. Error notifikasi
tidak membatalkan alur trading; periksa bagian `alerts` dalam laporan riset.

Ledger ditandai sebelum request agar restart tidak mengirim ulang secara buta.
Timeout/status `UNCONFIRMED` tidak dicoba ulang otomatis karena pesan mungkin
sudah diterima. Kebijakan ini dapat kehilangan alert saat gangguan jaringan;
bukan jaminan exactly-once delivery. Perintah `--test` baru adalah pengiriman baru.
Pengiriman tes kedua channel telah dikonfirmasi pengguna. Format branding dan
TP bertahap yang baru diuji offline; gunakan `alerts --preview` untuk contoh.

Referensi resmi: [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage),
[Discord Execute Webhook](https://discord.com/developers/docs/resources/webhook#execute-webhook).
