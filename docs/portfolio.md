# Portfolio OKX Demo

`python3 main.py run` sekarang berjalan sampai Ctrl+C. Maksimal **5 posisi aktif**,
satu posisi per pair. Scanner memilih hingga 20 perpetual USDT kripto secara
dinamis; lihat [discovery dan pelacakan sinyal](discovery-and-signals.md).
Setiap instrumen tetap harus tersedia/live di akun demo, lolos screening, memiliki
headline relevan, dan mendapat proposal AI valid. Lima slot adalah kapasitas,
bukan target jumlah posisi yang harus dipaksakan.

## Aturan posisi

- Margin maksimal 5 USDT, leverage 10–30x, risiko entry maksimal 1 USDT.
- TP1/TP2/TP3: 1R/2R/3R, pembagian awal 40%/35%/25%.
- Kuantitas dibulatkan ke lot exchange; sisa pembulatan masuk TP3. Jika tidak bisa
  dibagi menjadi tiga order dengan ukuran minimum yang valid, entry ditolak.
- TP native dipasang bersama entry FOK dan SL native, menggunakan harga mark.
  Harga TP dihitung dari harga limit entry dan SL awal sebelum submit; fill yang
  lebih baik dapat mengubah R aktual. Alert memakai harga referensi snapshot.
- Setelah fill TP1 terkonfirmasi, SL digeser ke entry aktual ± buffer biaya 0,2%.
  Setelah TP2, SL digeser minimal ke profit 1R. Buffer biaya tidak menjamin BEP
  bersih saat slippage, fee atau funding lebih tinggi dari perkiraan.
- Jika harga sudah melewati SL baru saat pengawasan berjalan, agent mengirim
  exit market reduce-only. Stop tidak pernah dilonggarkan.
- TP1/TP2 tetap memakai slot. Slot bebas setelah seluruh posisi tertutup, fill
  dan fee cocok, serta sisa algo milik posisi telah dibatalkan.
- Tidak ada lagi exit karena batas holding 60 detik. Masa berlaku sinyal membatasi
  entry saja; posisi yang telah terbuka dikelola sampai TP/SL/exit proteksi.

## Batas rugi dan publikasi

Budget awal 50 USDT diwarisi dari ledger lama (bukan direset ke 50). Equity
strategi dihitung dari fill/PnL/fee milik agent, funding terkait, dan unrealized
PnL posisi aktif; tidak mengambil seluruh saldo demo. Risiko sisa menuju stop
semua posisi dicadangkan sebelum entry baru, termasuk estimasi biaya keluar.

Kerugian mencapai 10% baseline hari WIB mengunci **entry baru** sampai hari
berikutnya, walaupun harga pulih di hari yang sama. Pengelolaan posisi dan
pencarian/publikasi sinyal tetap berjalan. Baseline pergantian hari menggunakan
nilai yang lebih tinggi antara snapshot terakhir dan snapshot pertama hari baru,
agar loss selama jeda/restart tidak terhapus. Ini konservatif, bukan rekonstruksi
harga persis tengah malam. Pengawasan berbasis polling tidak menangkap setiap tick.

Semua card signal memakai format sama tanpa label slot penuh, signal only, atau
status order. Pengiriman sinyal bukan bukti order terisi. Alasan entry ditolak
hanya muncul di output internal. Branding klik: Powered by [𝕏 web3rai](https://www.x.com/web3rai).

## Menjalankan dan menghentikan

```sh
python3 main.py run
# Opsional: berhenti setelah dua scan selesai
python3 main.py run --cycles 2
# Interval scan dan pemeriksaan posisi
python3 main.py run --interval 60 --poll 5
```

Riset berjalan sebagai subprocess sehingga supervisor dapat memantau posisi
selama AI menunggu respons. `--poll 5` adalah jeda antar pemeriksaan, bukan jaminan
latensi 5 detik; permintaan jaringan juga membutuhkan waktu.

**Ctrl+C tidak menutup posisi.** TP/SL native yang telah terkonfirmasi tetap berada
di OKX. Saat program mati, SL tidak naik otomatis setelah TP1/TP2; restart
`run` untuk melanjutkan pemeriksaan dan pergeseran SL. Jangan menghapus ledger
atau menjalankan bot lain pada pair yang sama. Gunakan akun demo khusus agent.
Posisi manual turut memakai kapasitas; exposure/order yang tidak dikenal memblokir
entry baru dan tidak diambil alih atau ditutup otomatis oleh bot.

State: `runtime/portfolio.sqlite3`; laporan: `runtime/portfolio-output.json` dan
`runtime/research-output.json`. State menyimpan identitas order sebelum submit.
Restart selalu merekonsiliasi identitas tersebut. Submit timeout/penolakan yang
belum dapat dibuktikan tidak dicoba ulang secara buta. Jika status tetap
`ENTRY_BLOCKED`, periksa error/jurnal dan akun OKX; jangan menghapus state untuk
membuka blokir. Posisi lain tetap diusahakan untuk dikelola.

Gap data funding lebih dari enam hari memblokir entry dan memerlukan rekonsiliasi
manual. Tidak ada reset otomatis saat bukti eksekusi/fee/funding tidak lengkap.
Legacy `agent --once`/`bounded-run` tidak dipakai lagi setelah ledger portfolio
terbentuk; `agent --recover` tetap tersedia untuk pending lama sebelum migrasi.

## Validasi

Tes offline mencakup slot penuh/slot kosong, daily halt, TP bertahap, SL naik,
restart, order ambigu, budgeting gabungan, fee/funding, dan publikasi saat halt.
Integrasi native split-TP baru belum diverifikasi melalui order OKX Demo sungguhan.
Implementasi tidak memaksa trade sintetis saat sinyal pasar belum lolos.

Parameter API mengacu pada [dokumentasi resmi OKX](https://www.okx.com/docs-v5/en/):
attached split TP/SL, amend algo, order details, serta funding bills. State
lokal dan respons exchange sama-sama diperlukan untuk konfirmasi eksekusi.
