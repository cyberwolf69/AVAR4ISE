"""Experimental OU/Zeta diagnostics. No execution imports or order capability."""
import math
import statistics


def regression(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance <= 1e-20:
        raise ValueError('Constant or numerically degenerate input')
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
    return my - slope * mx, slope


def hurst_rs(increments):
    """Classical R/S on log returns, not price levels; heuristic finite-scale slope."""
    upper = min(40, len(increments) // 2)
    if upper < 16:
        raise ValueError('Insufficient observations for R/S')
    lags = sorted({round(math.exp(math.log(8) + i * (math.log(upper) - math.log(8)) / 11)) for i in range(12)})
    points = []
    for lag in lags:
        ratios = []
        for offset in range(0, len(increments) - lag + 1, lag):
            chunk = increments[offset:offset + lag]
            sd, mean = statistics.pstdev(chunk), statistics.mean(chunk)
            if sd <= 1e-14:
                continue
            path, total = [0.0], 0.0
            for value in chunk:
                total += value - mean
                path.append(total)
            ratio = (max(path) - min(path)) / sd
            if ratio > 0:
                ratios.append(ratio)
        if ratios:
            points.append((math.log(lag), math.log(statistics.mean(ratios))))
    if len(points) < 6:
        raise ValueError('Insufficient usable R/S scales')
    _, slope = regression(*zip(*points))
    return slope


def analyze(prices, window=120):
    out = {'status': 'REJECTED', 'candidate': 'NONE', 'execution': 'DISABLED',
           'version': 1, 'model_input': 'LOG_CLOSE', 'hurst_input': 'TRAINING_LOG_RETURNS',
           'training_bars': window, 'threshold_zeta': 1.5, 'threshold_hurst': .45}
    try:
        if window < 120 or len(prices) < window + 1:
            raise ValueError('Need at least 120 training closes plus one scoring close')
        values = [float(x) for x in prices[-window - 1:]]
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError('Prices must be positive and finite')
        logs = list(map(math.log, values))
        training, current = logs[:-1], logs[-1]
        alpha, phi = regression(training[:-1], training[1:])
        out['phi'] = phi
        if not 0 < phi < 1:
            raise ValueError('OU requires 0 < phi < 1; no accepted mean estimate')
        residuals = [y - alpha - phi * x for x, y in zip(training, training[1:])]
        # Two OLS coefficients estimated from n paired observations.
        innovation_sd = math.sqrt(sum(e * e for e in residuals) / (len(residuals) - 2))
        if innovation_sd <= 1e-14:
            raise ValueError('Degenerate residual variance')
        stationary_sd = innovation_sd / math.sqrt(1 - phi * phi)
        mean = alpha / (1 - phi)
        theta = -math.log(phi)  # Unit time is one bar.
        z = (current - mean) / stationary_sd
        zeta = z * (1 - phi) / (1 + phi)
        hurst = hurst_rs([b - a for a, b in zip(training, training[1:])])
        out.update(status='OBSERVE', mu_log=mean, innovation_sd=innovation_sd,
                   stationary_sd=stationary_sd, theta_per_bar=theta,
                   half_life_bars=math.log(2) / theta, z=z, zeta=zeta, hurst=hurst,
                   reason='Experimental threshold not met')
        if not 0 < hurst < 1:
            raise ValueError('Finite-sample R/S estimate outside interpretable range')
        if abs(zeta) > 1.5 and hurst < .45:
            out.update(status='CANDIDATE', candidate='LONG' if zeta < 0 else 'SHORT',
                       reason='Experimental Zeta and Hurst gates passed; research only')
    except (ValueError, OverflowError) as exc:
        out.update(status='REJECTED', candidate='NONE', reason=str(exc))
    return out


def forward_diagnostics(prices, window=120, horizon=4, cost_fraction=.002):
    """Non-overlapping hypothetical close-to-close outcomes, never an execution backtest."""
    if horizon < 1 or not math.isfinite(cost_fraction) or cost_fraction < 0:
        raise ValueError('Invalid evaluation parameters')
    outcomes, evaluated, next_allowed = [], 0, window
    for index in range(window, len(prices) - horizon):
        result = analyze(prices[index - window:index + 1], window)
        evaluated += 1
        if result['status'] != 'CANDIDATE' or index < next_allowed:
            continue
        sign = 1 if result['candidate'] == 'LONG' else -1
        gross = sign * (float(prices[index + horizon]) / float(prices[index]) - 1)
        outcomes.append({'signal_index': index, 'exit_index': index + horizon,
                         'candidate': result['candidate'], 'gross_return': gross,
                         'return_after_assumed_cost': gross - cost_fraction})
        next_allowed = index + horizon
    return {'evaluated_windows': evaluated, 'candidate_count': len(outcomes),
            'horizon_bars': horizon, 'assumed_roundtrip_cost_fraction': cost_fraction,
            'funding_included': False, 'execution_simulated': False,
            'limitation': 'Close-to-close diagnostic only; no intrabar stop, fills, leverage, funding or portfolio simulation',
            'outcomes': outcomes,
            'mean_return_after_assumed_cost': statistics.mean(o['return_after_assumed_cost'] for o in outcomes) if outcomes else None}
