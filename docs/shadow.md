# Pembanding struktur dan konteks market

Diambil dari referensi diskusi: deteksi kondisi market sebagai konteks dan evaluasi hipotesis pada data baru. Tidak memasang QuantGPT, REA, AgenKit, atau provider model baru.

## Menjalankan

```sh
python3 main.py run
python3 main.py shadow
```

Perintah pertama menjalankan supervisor seperti biasa, ditambah worker pembanding terpisah. Perintah kedua hanya menampilkan laporan lokal. `python3 main.py shadow --update` menjalankan satu pembaruan: membaca snapshot lokal baru dan mengambil mark candle publik untuk outcome yang sedang dilacak. Tidak membutuhkan kunci trading untuk pembanding.

Restart proses `run` lama agar worker baru aktif. Strategi aktif tetap HH/HL atau LH/LL dengan BOS/retest. Paket analisis AI aktif sekarang mendapat konteks regime sehingga penilaian AI dapat berubah, tetapi gerbang struktur dan batas risiko tidak berubah.

## Apa yang dibandingkan

- **baseline**: definisi struktur aktif.
- **break_bias**: arah dari close-break pivot 1H terkonfirmasi, dipertahankan sampai invalidasi; hipotesis yang diuji dalam audit sebelumnya.

Keduanya memakai snapshot yang sama, dinilai ulang dengan waktu pengamatan worker, dan memakai syarat retest, spread serta ukuran risiko yang sama. Data yang menjadi kedaluwarsa ditolak. Snapshot lebih tua dari 180 detik tidak dibackfill. Quote memiliki batas umur scanner yang lebih ketat, sehingga keterlambatan worker dapat mengurangi sampel; alasan penolakan disimpan.

Saat pertama diaktifkan, cursor mulai dari ID scan terbaru. Snapshot lama tidak dijadikan sampel forward. Maksimal 200 scan diproses per pembaruan. Outcome diperbarui maksimal lima pair per model per putaran dengan rotasi; worker dibatasi 120 detik dan berjalan terpisah dari pengelolaan posisi.

Tidak ada panggilan AI, order, atau pengiriman Telegram/Discord dalam pembanding. Kandidat deterministik tidak diberi label sebagai rekomendasi AI. Catatannya berada di `runtime/shadow/<experiment>/`, terpisah dari transaksi, sinyal publik, dan mode improve. Satu BOS per pair/arah/model hanya didaftarkan sekali, termasuk saat proses diulang. Versi kode/konfigurasi baru memulai eksperimen terpisah.

## Konteks market

`metrics.regime` pada timeframe 15m dan 1H dihitung dari candle tertutup:

- Efisiensi 20 candle = perpindahan absolut / total perubahan absolut close. ≥0.4 diberi label DIRECTIONAL, ≤0.2 CHOPPY, sisanya MIXED.
- Arah perubahan close 20 candle: UP, DOWN, FLAT.
- Rata-rata true range 14 candle / rata-rata 28 candle sebelumnya: ≥1.5 EXPANDING, ≤0.75 CONTRACTING, sisanya NORMAL; baseline nol menghasilkan UNKNOWN.

Angka awal ini adalah hipotesis deskriptif, bukan probabilitas, kepastian arah, atau filter wajib. AI diminta mempertimbangkan konflik dengan setup, tanpa kewajiban menolak berdasarkan label saja. Snapshot mode improve menyimpan konteks tersebut untuk evaluasi berikutnya.

## Membaca hasil

Laporan: `runtime/shadow-output.json`. Menampilkan jumlah observasi, alasan penolakan, jumlah kandidat, TP1/2/3, SL, protected stop, expiry, timeout, serta data tidak lengkap. Hasil ini adalah **sentuhan harga referensi**, bukan fill atau PnL transaksi.

Pelacakan baru dimulai pada menit utuh berikutnya setelah kandidat dicatat. Jika urutan entry/TP/SL dalam satu candle tidak diketahui, hasil ditandai AMBIGUOUS; gap data tidak dianggap sukses atau gagal. Mesin menggunakan mark price 1m, bukan rekonstruksi tick eksekusi.

**Biaya, slippage, funding dan PnL bersih belum disimulasikan.** Tidak menghitung win rate atau mengaktifkan model otomatis dari jumlah kandidat/TP. Belum menguji penilaian AI atau ketersediaan slot akun pada alternatif. Backtest eksekusi setelah biaya tetap tahap terpisah sebelum keputusan aktivasi.
