# Penggunaan

Jalankan dari root repository dengan Python 3.11+ pada macOS/Linux. Kredensial
berada di `.env` root, bukan di source atau runtime.

## Konfigurasi lokal

```sh
# Hanya untuk checkout baru; jangan menimpa key yang sudah ada
cp .env.example .env
chmod 600 .env
```

Isi `APINEX_API_KEY`, `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, dan
`OKX_DEMO_PASSPHRASE`. Gunakan key **Demo Trading**. Set `AI_ENABLED=true` untuk
riset AI. Template memakai APINEX; ketersediaan model/harga mengikuti provider.
Semua signed request OKX memakai header demo, tanpa switch real trading.

Gunakan akun demo khusus bot. Exposure/order yang tidak dikenal memblokir entry
baru; agent tidak mengambil alih posisi manual.

## Perintah

Awali setiap perintah dengan `python3 -m hedging_tools`.

| Perintah | Fungsi |
|---|---|
| `paper-demo` | Siklus paper sintetis, offline |
| `risk-demo` | Contoh risiko, offline |
| `scan` | Screening market publik untuk pasangan konfigurasi paper |
| `universe` | Daftar/ranking hingga 20 pair dinamis, read-only |
| `improve` | Evaluasi SL/exit dari bukti lokal, tanpa mengubah strategi |
| `improve --evaluate` | Uji usulan filter entry pada training/holdout |
| `signals` | Ringkasan hasil sinyal referensi lokal |
| `signals --details` | Milestone per sinyal, terpisah dari trade |
| `signals --update` | Perbarui riwayat memakai candle publik |
| `research --ai` | Market, headline, AI; tanpa order |
| `preflight` | Baca akun demo |
| `agent --once --hold-seconds 60` | Satu siklus, dapat mengirim order demo |
| `run` | Portfolio maksimal 5 posisi, berjalan sampai Ctrl+C |
| `run --cycles 2` | Berhenti setelah dua scan; TP/SL native tetap aktif |
| `bounded-run --cycles 2` | Runner tes lama, sebelum migrasi portfolio |
| `agent --recover` | Rekonsiliasi pending, tanpa order |
| `reconcile runtime/demo-execution-….json` | Periksa jurnal tertentu |
| `execution-test --execute-demo-test` | Buka/tutup trade demo teknis tanpa sinyal AI |
| `ai-check --smoke-test` | Uji koneksi AI secara eksplisit |
| `evaluate-ai` | Uji skema AI dengan data sintetis |
| `paper-bridge` | Proposal ke ledger paper lokal |
| `reversion` | Riset OU/Zeta tanpa order |
| `alerts --preview` | Contoh alert sintetis lokal |
| `alerts --test` | Kirim tes ke Telegram/Discord yang diaktifkan |

Modul juga dapat dipanggil langsung, misalnya
`python3 -m hedging_tools.demo_runner --help`. Instalasi editable opsional:
`python3 -m pip install -e .`, lalu `hedging-tools run ...`.

Pengaturan channel dan token ada di [panduan alert](alerts.md).

## Hasil dan penghentian

Mode `run` menyimpan state pada `runtime/portfolio.sqlite3`, laporan pada
`portfolio-output.json`, dan analisis pada `research-output.json`. Jangan menghapus
ledger untuk menghilangkan blokir risiko. Budget lama di `demo-strategy.sqlite3`
diwarisi pada awal migrasi, dan pending lama harus direkonsiliasi terlebih dahulu.

Ctrl+C menghentikan scanning dan supervisor; posisi tetap memiliki TP/SL native
terkonfirmasi. Pergeseran SL setelah TP memerlukan program aktif. Restart `run`
untuk melanjutkan pengelolaan posisi. Lihat [aturan portfolio](portfolio.md) untuk
TP bertahap, batas rugi harian, risiko gabungan, dan pemulihan saat API bermasalah.

## Migrasi layout

Data lama dipindahkan dari root ke `runtime/`; `.env` tetap di root. Catatan
pengembangan dan backup database sebelum migrasi ada di `runtime/archive/`,
yang tidak ikut Git. Budget dan riwayat tidak direset.

Untuk upload, gunakan file yang ditampilkan Git, bukan ZIP seluruh folder kerja
yang dapat menyertakan `.env` dan data akun.
