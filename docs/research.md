# Eksperimen OU / Zeta

Referensi opsional ini tidak memengaruhi order bot.

```sh
python3 main.py reversion
```

Modul membaca hingga 300 candle BTC/ETH 15 menit. Fit AR(1) memakai 120 log-close
sebelumnya; candle scoring tidak ikut training. Guard phi dalam (0,1) bukan bukti
statistik stasioneritas. SD stasioner = SD residual / sqrt(1−phi²); Zeta memakai
Z-score tersebut dikali (1−phi)/(1+phi). Hurst R/S memakai return log training
pada skala 8–40 bar. Ambang |Zeta| > 1,5 dan H < 0,45 masih hipotesis.

Diagnostik forward menggunakan horizon empat bar, kandidat non-overlap, dan
asumsi biaya 0,2%. Belum ada simulasi fill, stop intrabar, leverage, funding atau
portfolio. Sampel pendek tidak cukup untuk menilai profitabilitas.

Referensi:

- [AR(1), Penn State](https://online.stat.psu.edu/stat510/Lesson01)
- [Bias estimasi Hurst](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2006WR005111)
- [OKX API](https://www.okx.com/docs-v5/)
