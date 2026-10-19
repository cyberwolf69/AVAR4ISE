# Hedging Tools

Agent perpetual untuk **OKX Demo** yang menggabungkan screening market, analisis
headline oleh AI, dan kontrol risiko persisten. Masih dalam tahap pengujian;
hasil simulasi belum membuktikan profitabilitas.

## Fitur

- [Scanner dinamis hingga 20 pair](docs/discovery-and-signals.md), lalu screening candle tertutup, tren, volume, dan spread.
- Riset headline dan proposal AI terstruktur; bot dapat memilih WAIT.
- Eksekusi demo isolated, stop-loss native, pengawasan posisi, dan exit reduce-only.
- Budget harian, risiko gabungan posisi, jurnal, dan rekonsiliasi fill/fee/funding/PnL.
- [Portfolio maksimal 5 posisi](docs/portfolio.md), TP1/TP2/TP3, dan SL naik setelah TP.
- [Mode improve](docs/improve.md): review SL dan usulan filter dengan uji holdout; tanpa auto-apply.
- Pelacakan TP/SL/kedaluwarsa semua sinyal referensi, terpisah dari PnL trade.
- [Alert Telegram dan Discord](docs/alerts.md) untuk kandidat sinyal AI tervalidasi.

## Mulai

Python **3.11+**, macOS atau Linux. Runtime memakai standard library Python.
Jalankan dari root repository; instalasi paket tidak diperlukan.

```sh
# Tes dan contoh offline — tanpa API key
python3 -B -m unittest discover -s tests -t . -v
python3 -B main.py paper-demo

# Daftar perintah
python3 main.py --help
```

Untuk API, salin `.env.example` ke `.env` **jika belum ada**, lalu isi key lokal.
Jangan commit `.env`. Lihat [panduan penggunaan](docs/usage.md).

```sh
# Baca akun demo tanpa order
python3 main.py preflight

# Market + headline + AI, tanpa order
python3 main.py research --ai

# Bisa membuka order DEMO jika sinyal dan pemeriksaan risiko lolos
python3 main.py run
```

Default `run`: terus berjalan sampai Ctrl+C, scan ulang 60 detik setelah riset
selesai, dan pemeriksaan posisi setiap jeda 5 detik. Ctrl+C mempertahankan TP/SL
native; pergeseran SL memerlukan program berjalan. Opsi: `python3 main.py run --help`.

## Batas awal

| Parameter | Nilai |
|---|---|
| Budget strategi | 50 USDT, terpisah dari saldo akun demo |
| Batas rugi harian | 10% equity strategi awal hari, zona Asia/Jakarta |
| Margin per trade | Maksimal 5 USDT |
| Leverage | 10–30x, dibatasi risiko berbasis stop |
| Risiko terencana | Maksimal 1 USDT atau sisa batas harian |
| Posisi bersamaan | Maksimal 5, satu per pair |
| Take profit | 1R/2R/3R, alokasi 40%/35%/25% mengikuti lot |

Batas risiko terencana bukan jaminan kerugian aktual saat gap/slippage.
Konfigurasi paper ada di [default.json](hedging_tools/config/default.json).
Aturan portfolio dijelaskan di [panduan portfolio](docs/portfolio.md). JSON paper
tidak mengatur semua batas executor demo. Perintah `bounded-run` menyimpan runner
tes lama untuk penggunaan sebelum migrasi portfolio.

## Struktur

```text
main.py             Pintu masuk CLI
hedging_tools/       Kode aplikasi dan konfigurasi default
tests/              Tes offline
docs/               Panduan, arsitektur, dan eksperimen
.github/workflows/  CI tes offline
runtime/            Database, laporan, jurnal, arsip lokal (diabaikan Git)
.env.example        Template konfigurasi tanpa key
pyproject.toml      Metadata proyek dan entry point opsional
```

Referensi OU/Zeta menjadi [eksperimen terpisah](docs/research.md), tanpa pengaruh
ke order. Perintah lama `python3 demo_runner.py` berubah menjadi
`python3 main.py run`; lihat [daftar perintah](docs/usage.md).

Setup aktif: BOS + retest market structure 1H/15m, dengan berita opsional. Aturan dan batas pengujian: [Market structure](docs/market-structure.md).

Pembanding struktur tanpa order dan konteks kondisi market: [Shadow comparison](docs/shadow.md). Lihat hasil dengan `python3 main.py shadow`.
