# Audit kandidat struktur — 19 Oktober 2026

Audit offline membaca 2.000 baris terakhir dari scans.sqlite3 secara read-only. Sebanyak 1.377 observasi memiliki hasil model struktur dan dapat dibandingkan; 623 lainnya tidak memiliki marker model struktur (termasuk error sebelum deteksi struktur dan hasil model lain). Rentang observasi yang dibandingkan: **13:45–19:05 WIB**. Snapshot candle yang berulang bukan sampel independen.

| Model | Observasi lolos scanner | Setup BOS unik |
|---|---:|---:|
| HH/HL atau LH/LL saat ini | 0 | 0 |
| Hipotesis arah dari close-break swing 1H terkonfirmasi | 23 | 6 |

Alternatif menetapkan arah ketika close melintasi pivot 1H yang telah tersedia sebelum candle tersebut. Arah dipertahankan sampai swing pelindung ditembus; break berlawanan dapat mengubah arah. Setiap pivot hanya dipakai sekali. Ini hipotesis riset, bukan bukti aktivitas institusi. Deteksi BOS/retest 15m, batas entry 0.75 ATR, stop struktur, spread, kesegaran data, dan pemeriksaan ukuran risiko scanner sama untuk kedua model.

Kandidat pertama per event pada alternatif:

| Pair | Arah | Waktu observasi pertama (WIB) |
|---|---|---|
| HYPE | LONG | 14:00 |
| FIL | LONG | 14:00 |
| XRP | LONG | 14:45 |
| UNI | LONG | 14:47 |
| BTC | LONG | 16:48 |
| LIT | SHORT | 16:48 |

Sebanyak 1.126 observasi tertahan di definisi struktur 1H model aktif. Pada alternatif, 842 observasi tertahan karena BOS 15m sudah kedaluwarsa. Jadi memperbaiki pengenalan arah tidak otomatis menghasilkan entry pada semua pair.

## Batas kesimpulan

Ini audit ketersediaan kandidat, **bukan backtest PnL**. Tidak ada pemanggilan AI, validasi saldo/slot akun saat itu, simulasi fill, notifikasi, atau order. Keenam kandidat belum bisa disebut transaksi layak atau menang. Risiko scanner memperhitungkan batas ukuran, tetapi bukan seluruh gate eksekusi portfolio. Tidak ada pemilihan parameter berdasarkan profit pada data ini.

Strategi aktif **belum berubah**. Langkah yang disarankan adalah menjalankan alternatif sebagai pembanding tanpa eksekusi pada data baru, mencatat outcome dengan biaya dan urutan TP/SL yang benar, kemudian mengusulkan perubahan setelah hasilnya tersedia. Jumlah sinyal saja bukan alasan mengaktifkan model.

## Mengulang audit

```sh
python3 -B -m hedging_tools.structure_audit --limit 2000
```

Perintah membaca database lokal dan menulis runtime/structure-audit.json. Karena memakai baris terbaru, hasil bisa berbeda setelah database bertambah. Laporan JSON mencatat ID scan pertama setiap event, waktu, level, ukuran, alasan penolakan, serta transisi antar-model. Database sumber tidak dimodifikasi. Modul riset berdiri sendiri dan tidak diimpor runner trading.
