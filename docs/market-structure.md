# Setup market structure v1

Scanner default sekarang `structure_bos_retest_v1`. Ini implementasi mandiri BOS/retest, bukan port kode LuxAlgo atau replika hasil indikator TradingView. Order block dan FVG belum menjadi detektor aktif. Referensi konsep: [SMC LuxAlgo](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo/), [Order Block Detector](https://www.tradingview.com/script/KvGhxGxY-Order-Block-Detector-LuxAlgo/). Backtester LuxAlgo merupakan referensi evaluasi; belum terintegrasi.

## Aturan yang dapat diuji

- Semua candle sudah tutup; swing memakai dua candle kiri dan dua kanan, high/low harus lebih ekstrem secara ketat. Pivot baru tersedia setelah candle kanan kedua tutup.
- Struktur 1H: dua swing high dan dua swing low terbaru sama-sama naik (long), atau turun (short). Harga tidak boleh sudah menembus swing invalidasi terakhir.
- BOS 15m: close melintasi swing searah tren yang sudah dikonfirmasi sebelum candle breakout. Satu pivot hanya menghasilkan satu BOS.
- Retest terjadi 1–8 candle setelah BOS. Candle terakhir harus menyentuh area level ±0.25 ATR, menutup kembali di sisi arah setup, dan close bergerak searah dibanding candle sebelumnya.
- Close melewati sisi salah level lebih dari 0.25 ATR sejak breakout membatalkan retest itu.
- Quote entry harus tetap di sisi benar level dan tidak lebih jauh dari 0.75 ATR.
- SL berada di luar low/high rangkaian candle setelah breakout, ditambah buffer 0.2 ATR. Ukuran posisi tetap harus lolos batas margin dan risiko yang ada. Tidak mengencangkan SL agar setup dipaksa lolos.

Angka tersebut merupakan parameter awal yang belum terbukti menghasilkan keuntungan. EMA dan RVOL disampaikan sebagai konteks AI, tidak menjadi gerbang wajib pada model struktur. Syarat spread, kesegaran data, lot, leverage, serta risiko tetap berlaku. Portfolio tetap maksimal 5 posisi; konfigurasi legacy paper memiliki batasnya sendiri.

## AI, berita, dan log

Kandidat teknikal boleh ditinjau tanpa berita. Packet tetap berisi `news` dan `feed_health`, sehingga AI dapat membedakan feed gagal dari tidak adanya headline relevan. AI tidak boleh menyebut ketiadaan berita sebagai bukti aman, mengarang sumber, membalik arah kandidat, atau mengubah risiko. Batas tiga review AI per siklus dan rotasi pair tetap berlaku.

`SCAN_COMPLETE.decisions[].screen_reasons` menjelaskan alasan scanner menolak setup. Bukti struktur tersedia dalam `metrics.structure` di runtime/research-output.json, termasuk waktu asal pivot, candle konfirmasi pivot, BOS, dan konfirmasi retest. Timestamp itu adalah waktu pembukaan candle; bukti hanya tersedia setelah candle tersebut selesai.

Mode improve merekam bukti dan versi strategi baru secara terpisah. Evaluasinya tetap replay filter entry, belum merupakan backtest penuh strategi struktur. Uji demo diperlukan untuk menilai hasil aktual.

Jalankan setelah menghentikan proses versi lama:

```sh
python3 main.py run
```

Restart tidak menutup posisi. Pengelolaan posisi yang sudah ada memakai rencana tersimpan; perubahan scanner berlaku untuk kandidat baru.
