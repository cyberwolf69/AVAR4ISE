# Mode improve: evaluasi sebelum perubahan

Mode ini aktif sebagai worker lokal terpisah ketika menjalankan `python3 main.py run`.
Sekitar setiap 60 detik worker membaca riwayat terbaru, membuat review untuk SL,
stop setelah TP, exit proteksi, dan posisi rugi. Evaluasi tidak mengganggu
pengawasan posisi. Tidak ada API AI, order, pesan channel, atau perubahan strategi
yang dikirim oleh mode improve.

```sh
# Review bukti yang sudah tersedia
python3 main.py improve
# Review dan uji calon filter entry pada data historis lokal
python3 main.py improve --evaluate
# Sertakan detail bukti setiap kasus
python3 main.py improve --evaluate --details
```

## Bukti yang disimpan mulai sekarang

Entry menyimpan versi strategi, snapshot indikator, harga referensi, quote saat
pengambilan keputusan, alasan/invalidation AI, ID sumber berita, serta budget
risiko dan posisi aktif. Selama posisi berjalan, perubahan SL yang terkonfirmasi
ikut dicatat. Rekonsiliasi menyimpan identitas fill entry/exit, role TP/SL,
kuantitas, harga, fee, PnL order, waktu, dan funding bill terkait posisi.

Sumber utama tetap `portfolio.sqlite3` dan `signal-history.sqlite3`. Improve
membukanya dalam mode read-only. Laporan: `runtime/improve-output.json`.
Riwayat review dan proposal: `runtime/improve.sqlite3`; revisi berdasarkan hash
bukti, sehingga membaca ulang tidak menggandakan kasus yang sama.

Bukti baru tidak direkayasa untuk posisi lama. Data lama yang tidak mempunyai
snapshot/identitas exit lengkap ditandai tidak cukup atau tidak diketahui.

## Menafsirkan review

- `SL`: ada fill dari algo stop milik posisi yang terhubung ke rekonsiliasi.
- `PROTECTED_STOP`: stop setelah sebagian TP. Ini tidak otomatis berarti trade rugi.
- `PROTECTIVE_EXIT`: penutupan karena masalah proteksi, misalnya SL tidak ditemukan.
- `UNKNOWN`: rugi/penutupan tanpa bukti yang cukup untuk menyebutnya SL.

Spread tinggi, volume relatif rendah, dan snapshot berumur lama ditampilkan
sebagai **observasi**, bukan kepastian penyebab SL. Tanpa bukti sebab-akibat,
kesimpulannya `CAUSE_NOT_ESTABLISHED`. Kerugian melebihi risk budget juga ditandai
untuk pemeriksaan. Satu SL tidak langsung membuat strategi dinyatakan salah.

Review dan pola **trade demo aktual** terpisah dari **sinyal referensi**. PnL
aktual yang dipakai evaluasi adalah total PnL order setelah fee, **belum termasuk
funding**; funding yang tercatat ditampilkan terpisah. Sinyal referensi tidak
diberi PnL buatan. Sinyal ambigu, data bolong, kedaluwarsa, dan timeout tidak masuk
uji hasil terminal. Ini dapat menimbulkan selection bias, jadi laporan bukan
angka performa seluruh sinyal.

## Uji calon perbaikan

Versi pertama hanya menguji tiga filter entry yang ditentukan sebelumnya:

1. RVOL minimal 1,2.
2. Spread maksimal 0,05%.
3. Umur snapshot pada keputusan entry maksimal 60 detik.

Ini replay keputusan **ambil/lewati entry** pada hasil historis yang teramati,
bukan simulasi fill baru atau backtest perubahan SL. Level TP/SL, ukuran posisi,
leverage dan risiko tidak dioptimasi. Interaksi posisi, penggunaan ulang modal,
serta peluang baru akibat slot yang kosong tidak dimodelkan.

Dataset dipisahkan berdasarkan sumber dan versi strategi. Dua pertiga awal
menjadi training, sepertiga terakhir menjadi holdout. Hasil training yang masih
berlangsung saat holdout mulai dibuang agar tidak memakai hasil dari masa depan.
Minimal 20 sampel training, 10 holdout, dan 5 SL awal di training diperlukan.
Jika belum cukup, laporan menampilkan `INSUFFICIENT_DATA`, tanpa usulan palsu.

Calon filter harus mempertahankan minimal 70% sampel dan 80% hasil positif/TP3,
menolak minimal dua sampel, dan memperbaiki metrik pada training. Hanya pemenang
training diuji pada holdout; jika gagal, sistem tidak mencoba kandidat lain
menggunakan hasil holdout tersebut sebagai petunjuk.

Untuk trade aktual, metriknya jumlah PnL order setelah fee tanpa funding. Untuk
referensi, metriknya jumlah SL awal, dengan syarat retensi TP3. Keduanya tidak
digabung menjadi satu skor. Uji berulang pada data yang berkembang masih bersifat
eksploratif dan tidak menjamin hasil di masa depan; pengujian forward pada data
baru tetap diperlukan sebelum mengandalkan perubahan.

## Persetujuan dan versi

Hasil yang lolos berstatus `NEEDS_USER_REVIEW`, dengan ID proposal, hash dataset,
ID sampel training/holdout, rule yang diusulkan, dan hasil pembanding. Status
`HOLDOUT_FAILED` atau `NO_SUPPORTED_CHANGE` tidak layak dipromosikan dari bukti ini.

**Tidak ada jalur auto-apply atau perintah approve pada versi ini.** Sampaikan ID
proposal yang ingin dibahas. Perubahan kode/config baru dilakukan setelah
persetujuan terpisah atas perubahan konkret; migrasi versi dan rollback disiapkan
bersama perubahan yang disetujui, bukan dilakukan diam-diam oleh worker.

Batas yang tetap terkunci: maksimal 5 posisi, margin maksimal 5 USDT, leverage
10–30x, risk budget entry maksimal 1 USDT, dan rugi harian 10%. Stop tidak dilebarkan
untuk mengejar loss, dan hasil review tidak mereset daily halt.
