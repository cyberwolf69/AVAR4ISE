# Scanner dinamis dan riwayat sinyal

`python3 main.py run` memakai dua fitur ini otomatis. Kapasitas trading tetap
maksimal **5 posisi**, satu per pair, dengan margin/risk budget yang sama.
Pair yang keluar dari daftar scanner tetap dikelola jika posisinya masih terbuka.

## Pemilihan hingga 20 pair

Setiap scan mengambil daftar instrumen dan ticker publik OKX yang baru. Syarat:

- SWAP kripto kategori `1`, linear, settlement USDT, status live.
- Metadata ukuran kontrak valid dan leverage instrumen mendukung minimal 10x.
- Estimasi turnover 24 jam minimal **10 juta USDT**.
- Spread bid/ask maksimal **0,1%** dan timestamp ticker tidak lebih tua 30 detik.

Estimasi turnover = `volCcy24h × last`. Pada kontrak derivatif OKX,
`volCcy24h` adalah jumlah aset dasar, bukan jumlah kontrak atau nilai USDT.
Ini estimasi memakai harga terakhir, bukan total notional historis yang eksak.

Ranking memakai 60% likuiditas relatif, 20% kualitas spread, dan 20% aktivitas
rentang harga yang dibobot likuiditas. Aktivitas dibatasi pada rentang 10%, agar
lonjakan ekstrem tidak terus memperbesar skor. Skor bukan probabilitas menang.
Maksimal 20 pair dengan skor tertinggi masuk screening candle yang sudah ada.
Jika discovery gagal/kosong, tidak memakai daftar lama untuk membuka entry baru.

Pengambilan snapshot memakai hingga empat worker. Maksimal tiga panggilan AI per
scan portfolio dan hanya untuk kandidat dengan bukti headline. Prioritas diberikan
kepada pair yang paling lama belum direview; ranking menjadi pemecah seri. Cursor
persisten mencegah tiga pair teratas terus menghabiskan seluruh jatah AI. Kandidat lain ditandai `AI_CALL_BUDGET_REACHED`, bukan dipaksakan jadi
sinyal. Snapshot lebih tua dari tiga menit sebelum review AI dilewati. Request pasar publik tidak
mengirim key akun. Kegagalan satu pair tidak membatalkan kandidat valid
pair lain; alur risiko dan revalidasi order tetap wajib.

Nama token dikenal dipakai untuk pencarian headline; ticker baru/ambigu memerlukan
cashtag `$TOKEN` atau simbol `TOKENUSDT`/`TOKEN/USDT`. Berita BTC tidak otomatis
dianggap bukti untuk semua altcoin. Jika tidak ada headline relevan, tidak ada
proposal directional. Penambahan pair tidak menjamin lebih banyak trade.

```sh
# Read-only, tanpa key akun: lihat daftar pilihan sekarang
python3 main.py universe
# Riset dan alert saja; tidak membuka order
python3 main.py research --ai --portfolio
```

Daftar/ranking terakhir: `runtime/universe-output.json`.

## Apa yang dilacak

Setiap proposal LONG/SHORT yang tervalidasi dicatat, termasuk ketika alert
nonaktif, slot penuh, atau entry diblokir risiko. WAIT tidak dicatat sebagai
sinyal. Identitas memakai pair + arah + bucket 15 menit, sama dengan deduplikasi
alert; snapshot pertama dipertahankan, tidak ditulis ulang dengan harga terbaru.
Pencatatan dimulai saat proposal diterima sebagai sinyal, bukan saat snapshot
pasar dibuat. Ini bukan konfirmasi bahwa pesan channel telah terkirim.

Model evaluasi referensi:

1. Gunakan **candle mark-price 1 menit yang sudah selesai**, mulai dari menit penuh
   pertama setelah sinyal dibuat. Candle yang melintasi waktu pembuatan dilewati
   agar pergerakan sebelum sinyal tidak masuk penilaian. Pergerakan pada sisa menit
   awal itu tidak tercakup dalam model ini.
2. Tunggu harga referensi entry tersentuh sebelum masa berlaku proposal berakhir
   (umumnya 15 menit sejak paket riset dibuat). Ini asumsi *reference touch*,
   bukan simulasi fill FOK yang dipakai executor.
3. Setelah entry referensi tersentuh, amati maksimum 24 jam. TP1/TP2/TP3 dan SL
   mengikuti level referensi. Setelah TP1, stop model memakai buffer biaya 0,2%;
   setelah TP2 minimal profit 1R. Ini model level harga, bukan estimasi PnL bersih.
4. Urutan yang tidak bisa dibuktikan dari OHLC diberi `AMBIGUOUS`: entry dan exit
   di candle sama, TP dan SL di candle sama, atau TP dan stop baru di candle sama.
   Jangan menghitungnya sebagai kemenangan maupun kerugian yang pasti.

| Status | Arti |
|---|---|
| PENDING | Menunggu entry referensi tersentuh |
| ACTIVE | Entry referensi tersentuh; TP1/TP2 dicatat sebagai milestone |
| TP3 | Seluruh target referensi tercapai tanpa ambiguitas terdeteksi |
| SL | Stop awal referensi tersentuh |
| PROTECTED_STOP | Stop setelah TP1/TP2 tersentuh |
| EXPIRED | Tidak ada entry dalam masa berlaku pada timeline yang teramati |
| TIMEOUT | Observasi 24 jam berakhir tanpa hasil terminal lain |
| AMBIGUOUS | Urutan intrabar tidak diketahui |

Data yang hilang/gagal diambil ditandai `DATA_GAP`/`DATA_ERROR`, bukan dianggap
expired atau menang. Cursor disimpan di SQLite; restart melanjutkan candle yang
belum diproses. Setiap pembaruan membaca maksimum satu jendela 100 menit per pair,
hingga 20 pair; backlog setelah mati lama diproses bertahap. Sinyal tidak berhenti
dilacak hanya karena pair keluar dari daftar 20 terbaru.

Worker terpisah diperbarui sekitar setiap 60 detik, dengan timeout 120 detik;
latensi API dapat menambah jeda. Tetap berjalan saat slot penuh/daily halt.
Ctrl+C menghentikan worker bersama supervisor; riwayat dilanjutkan pada restart.
Tidak ada pesan TP/SL baru atau edit card otomatis dari tracker ini.

```sh
# Ringkasan lokal, tanpa request jaringan
python3 main.py signals
# Lihat setiap sinyal beserta urutan milestone
python3 main.py signals --details
# Perbarui dari API publik secara manual, tanpa order atau alert
python3 main.py signals --update
```

Ledger: `runtime/signal-history.sqlite3`. Laporan: `runtime/signal-history-output.json`.
Ledger ini **terpisah dari `portfolio.sqlite3`**. Jumlah TP tersentuh bukan jumlah
trade profit. Tracker tidak menghitung fill, fee, funding, win rate, maupun PnL
aktual. Sinyal lama sebelum fitur aktif tidak direkonstruksi dengan waktu terbit
buatan; riwayat dimulai dari proposal baru.

Parameter pasar mengikuti [dokumentasi OKX](https://www.okx.com/docs-v5/en/),
khususnya instruments, tickers, dan historical mark-price candles.
