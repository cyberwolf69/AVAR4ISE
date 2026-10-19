> Mode `run` kini memakai portfolio persisten lima posisi. Lihat [portfolio](portfolio.md)
> untuk alur aktif. Penjelasan executor satu posisi di bawah merupakan jalur tes legacy.

# Arsitektur

Alur: market publik → screening → headline → proposal AI tervalidasi → kontrol
risiko → OKX Demo → pengawasan posisi → rekonsiliasi → settlement budget.

| Modul | Tanggung jawab |
|---|---|
| `okx_public`, `scanner`, `scan` | Data publik dan screening |
| `news`, `analyst`, `ai_client`, `research` | Bukti headline, AI, validasi proposal |
| `risk_engine`, `paper`, `paper_bridge` | Risiko dan ledger simulasi lokal |
| `okx_demo` | Signed demo request pada route terbatas |
| `demo_execution`, `demo_monitor` | Entry, SL, target lokal 2R, time exit, cleanup |
| `reconcile_demo` | Bukti fill/fee/PnL dan pengaitan order native stop |
| `demo_agent` | Budget, satu siklus, daily halt, recovery, cooldown |
| `demo_runner` | Loop terbatas, kunci proses, riwayat siklus |
| `paths` | Lokasi konfigurasi dan runtime |

## Kontrol

AI tidak dapat mengubah batas risiko. Leverage demo mencoba 30x turun ke 10x,
dibatasi instrumen dan risiko stop plus estimasi biaya 0,2% notional. Uji teknis
tanpa sinyal tetap 10x. Entry IOC memakai attached SL; exit bot reduce-only.
Monitor memeriksa posisi aktual, stop live, mark price, dan buffer likuidasi.

Settlement mencocokkan seluruh fill exit (termasuk native stop) dengan entry,
serta perubahan equity dengan PnL setelah fee. Bukti disimpan atomik. Pending,
batas rugi, proposal terpakai dan cooldown bertahan setelah restart. Kunci proses
mencegah agent, runner, dan recovery berjalan bersamaan.

## Keterbatasan

- Belum daemon 24/7 dan belum ada bukti profitabilitas strategi.
- Target 2R/time exit berjalan lokal; hanya SL berada di exchange.
- Harga likuidasi aktual tersedia setelah entry. Gap/slippage dapat melampaui risk.
- Funding belum dipisahkan otomatis dari perubahan equity saat settlement.
- Recovery memblokir perpindahan hari dan bukti yang tidak lengkap.
- Sebagian batas demo masih konstanta; JSON terutama untuk paper/screening.
- Pengujian native stop terpicu dan multi-trade belum lengkap di exchange.
- Paket ditujukan untuk checkout lokal/editable: `.env` dan `runtime/` relatif
  ke root checkout, bukan direktori kerja shell.

Tes tidak membutuhkan key atau jaringan. CI dikonfigurasi untuk Python 3.11–3.14;
hasil CI baru tersedia setelah workflow dijalankan di GitHub.
