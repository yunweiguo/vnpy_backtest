import { useEffect, useMemo, useState } from 'react'
import { diagnostics, fetchArtifact, getBacktest, listArtifacts, resolveProfile, submitBacktest, validateConfig } from './api'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts'

const presets = [
  { id: 'balanced', label: 'Balanced (CSP/SPV)' },
  { id: 'conservative', label: 'Conservative (CSP/SPV)' },
  { id: 'aggressive', label: 'Aggressive (CSP/SPV)' },
  { id: 'call_verticals', label: 'Call Verticals (SCV/LCV)' },
  { id: 'iron_condor', label: 'Iron Condor (IC)' },
]

type Status = 'idle' | 'loading' | 'done' | 'error'
type TradeRow = { date: string; symbol: string; action: string; kind: string; net: number | null; pnl: number; cum: number; dd: number }

export default function App() {
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8000')
  const [configText, setConfigText] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [message, setMessage] = useState('')
  const [runId, setRunId] = useState('')
  const [artifacts, setArtifacts] = useState<string[]>([])
  const [metrics, setMetrics] = useState<any | null>(null)
  const [tradesData, setTradesData] = useState<TradeRow[]>([])
  const [marketPreview, setMarketPreview] = useState<string>('')
  const [diagnosticOutput, setDiagnosticOutput] = useState<any | null>(null)
  const [diagParams, setDiagParams] = useState({ market: 'US', symbol: 'AAPL', session_date: '2024-06-03', dte_min: 25, dte_max: 45, delta_lo: 0.18, delta_hi: 0.25, kinds: 'CSP,SPV' })

  const configObj = useMemo(() => {
    try {
      return configText ? JSON.parse(configText) : null
    } catch (err) {
      return null
    }
  }, [configText])

  const setPreset = async (preset: string) => {
    setStatus('loading')
    try {
      const res = await resolveProfile(preset, baseUrl)
      setConfigText(JSON.stringify(res.normalized_config, null, 2))
      setMessage(`Preset ${preset} loaded`)
      setStatus('done')
    } catch (err: any) {
      setMessage(err.message)
      setStatus('error')
    }
  }

  const doValidate = async () => {
    if (!configObj) {
      setMessage('配置 JSON 无法解析')
      setStatus('error')
      return
    }
    setStatus('loading')
    try {
      const res = await validateConfig(configObj, baseUrl)
      setMessage(`校验通过，fingerprint=${res.fingerprint}`)
      setStatus('done')
    } catch (err: any) {
      setMessage(err.message)
      setStatus('error')
    }
  }

  const doSubmit = async () => {
    if (!configObj) {
      setMessage('配置 JSON 无法解析')
      setStatus('error')
      return
    }
    setStatus('loading')
    try {
      const res = await submitBacktest(configObj, baseUrl)
      setRunId(res.run_id)
      setMessage(`已提交 run_id=${res.run_id}`)
      setStatus('done')
    } catch (err: any) {
      setMessage(err.message)
      setStatus('error')
    }
  }

  const refreshRun = async () => {
    if (!runId) return
    setStatus('loading')
    try {
      const res = await getBacktest(runId, baseUrl)
      setMessage(`run ${runId} 状态: ${res.status} ${res.message || ''}`)
      const arts = await listArtifacts(runId, baseUrl)
      setArtifacts(arts.artifacts)
      // auto load metrics/trades if存在
      if (arts.artifacts.includes('metrics.json')) {
        try {
          const m = await fetchArtifact(runId, 'metrics.json', baseUrl)
          setMetrics(m)
        } catch (err) {
          console.error(err)
        }
      }
      if (arts.artifacts.includes('trades.csv')) {
        try {
          const text = await fetchArtifact(runId, 'trades.csv', baseUrl, true)
          const lines = text.split('\n')
          setTradesData(parseTrades(lines))
        } catch (err) {
          console.error(err)
        }
      }
      if (arts.artifacts.includes('market_data.csv')) {
        try {
          const text = await fetchArtifact(runId, 'market_data.csv', baseUrl, true)
          const lines = text.split('\n').slice(0, 20).join('\n')
          setMarketPreview(lines)
        } catch (err) {
          console.error(err)
        }
      } else {
        setMarketPreview('')
      }
      setStatus('done')
    } catch (err: any) {
      setMessage(err.message)
      setStatus('error')
    }
  }

  const doDiagnostics = async () => {
    setStatus('loading')
    try {
      const params = new URLSearchParams()
      Object.entries(diagParams).forEach(([k, v]) => params.set(k, String(v)))
      const res = await diagnostics(params, baseUrl)
      setDiagnosticOutput(res)
      setMessage('诊断成功')
      setStatus('done')
    } catch (err: any) {
      setMessage(err.message)
      setStatus('error')
    }
  }

  useEffect(() => {
    // load default preset on mount
    setPreset('balanced')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="app">
      <header>
        <div className="brand">Strategy Debugger</div>
        <div className="status">状态: {status} {message && `| ${message}`}</div>
      </header>

      <section className="panel">
        <div className="row">
          <label>API Base</label>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </div>
        <div className="row">
          <label>预设</label>
          <div className="preset-list">
            {presets.map((p) => (
              <button key={p.id} onClick={() => setPreset(p.id)}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="panel two-col">
        <div>
          <div className="panel-title">配置 (JSON)</div>
          <textarea value={configText} onChange={(e) => setConfigText(e.target.value)} spellCheck={false} />
          <div className="actions">
            <button onClick={doValidate}>校验</button>
            <button onClick={doSubmit}>提交回测</button>
            <button onClick={refreshRun} disabled={!runId}>
              查询 run 状态
            </button>
          </div>
          {runId && <div className="hint">当前 run_id: {runId}</div>}
        </div>

        <div>
          <div className="panel-title">诊断 (/diagnostics/option-chain)</div>
          <div className="grid">
            <label>Market</label>
            <input value={diagParams.market} onChange={(e) => setDiagParams({ ...diagParams, market: e.target.value })} />
            <label>Symbol</label>
            <input value={diagParams.symbol} onChange={(e) => setDiagParams({ ...diagParams, symbol: e.target.value })} />
            <label>Date</label>
            <input value={diagParams.session_date} onChange={(e) => setDiagParams({ ...diagParams, session_date: e.target.value })} />
            <label>DTE Min</label>
            <input type="number" value={diagParams.dte_min}
              onChange={(e) => setDiagParams({ ...diagParams, dte_min: Number(e.target.value) })} />
            <label>DTE Max</label>
            <input type="number" value={diagParams.dte_max}
              onChange={(e) => setDiagParams({ ...diagParams, dte_max: Number(e.target.value) })} />
            <label>Δ Lo</label>
            <input type="number" step="0.01" value={diagParams.delta_lo}
              onChange={(e) => setDiagParams({ ...diagParams, delta_lo: Number(e.target.value) })} />
            <label>Δ Hi</label>
            <input type="number" step="0.01" value={diagParams.delta_hi}
              onChange={(e) => setDiagParams({ ...diagParams, delta_hi: Number(e.target.value) })} />
            <label>Kinds</label>
            <input value={diagParams.kinds} onChange={(e) => setDiagParams({ ...diagParams, kinds: e.target.value })} />
          </div>
          <button onClick={doDiagnostics}>诊断</button>
          {diagnosticOutput && (
            <pre className="output">{JSON.stringify(diagnosticOutput, null, 2)}</pre>
          )}
        </div>

        <div>
          <div className="panel-title">回测产物</div>
          <button onClick={refreshRun} disabled={!runId}>
            刷新状态/产物
          </button>
          {!runId && <div className="hint">先提交回测获得 run_id</div>}
          {artifacts.length > 0 && (
            <div className="hint">产物: {artifacts.join(', ')}</div>
          )}
          {metrics && (
            <div>
              <div className="panel-subtitle">核心指标</div>
              <div className="metrics-grid">
                <MetricCard label="总收益" value={metrics.net_pnl} fmt="currency" />
                <MetricCard label="年化收益率" value={metrics.annualized_return} fmt="percent" />
                <MetricCard label="夏普比率" value={metrics.sharpe_ratio} fmt="float" />
                <MetricCard label="最大回撤" value={metrics.max_drawdown} fmt="currency" />
                <MetricCard label="最大回撤%" value={metrics.max_drawdown_pct} fmt="percent" />
                <MetricCard label="胜率" value={metrics.win_rate} fmt="percent" />
                <MetricCard label="盈亏比" value={computeProfitFactor(tradesData)} fmt="float" />
                <MetricCard label="笔均净收益" value={avgPnl(tradesData)} fmt="currency" />
                <MetricCard label="笔数" value={tradesData.length} fmt="int" />
                <MetricCard label="Entries" value={metrics.entries} fmt="int" />
                <MetricCard label="Exits" value={metrics.exits} fmt="int" />
                <MetricCard label="Rolls" value={metrics.rolls} fmt="int" />
              </div>
            </div>
          )}
          {tradesData.length > 0 && (
            <div>
              <div className="panel-subtitle">累计 PnL（按 trades.csv 顺序）</div>
              <div className="chart">
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={tradesData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" hide={tradesData.length > 30} />
                    <YAxis />
                    <Tooltip />
                    <Line type="monotone" dataKey="cum" stroke="#0066cc" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="panel-subtitle">回撤曲线</div>
              <div className="chart">
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={tradesData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" hide={tradesData.length > 30} />
                    <YAxis />
                    <Tooltip />
                    <Area type="monotone" dataKey="dd" stroke="#d64545" fill="#f9d8d8" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
          {tradesData.length > 0 && (
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>符号</th>
                    <th>动作</th>
                    <th>策略</th>
                    <th>净入/出</th>
                    <th>PNL</th>
                    <th>累计</th>
                  </tr>
                </thead>
                <tbody>
                  {tradesData.slice(-50).reverse().map((t, idx) => (
                    <tr key={idx}>
                      <td>{t.date}</td>
                      <td>{t.symbol}</td>
                      <td>{t.action}</td>
                      <td>{t.kind}</td>
                      <td>{t.net === null ? '-' : t.net.toFixed(2)}</td>
                      <td className={t.pnl >= 0 ? 'pos' : 'neg'}>{t.pnl.toFixed(2)}</td>
                      <td className={t.cum >= 0 ? 'pos' : 'neg'}>{t.cum.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="hint">显示最近 50 条交易，完整文件可下载 trades.csv</div>
            </div>
          )}
          {marketPreview && (
            <div>
              <div className="panel-subtitle">market_data.csv (前 20 行)</div>
              <pre className="output">{marketPreview}</pre>
            </div>
          )}
          {artifacts.length > 0 && (
            <div className="artifact-links">
              {artifacts.map((fn) => (
                <a key={fn} href={`${baseUrl}/backtests/${runId}/artifacts/${fn}`} target="_blank" rel="noreferrer">
                  下载 {fn}
                </a>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

type MetricCardProps = { label: string; value: any; fmt?: 'currency' | 'percent' | 'int' | 'float' }

function MetricCard({ label, value, fmt }: MetricCardProps) {
  const display = formatValue(value, fmt)
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{display}</div>
    </div>
  )
}

function parseTrades(lines: string[]) {
  if (!lines.length) return []
  const header = lines[0].split(',')
  const idxDate = header.indexOf('date')
  const idxPnl = header.indexOf('pnl')
  const idxSym = header.indexOf('symbol')
  const idxAction = header.indexOf('action')
  const idxKind = header.indexOf('kind')
  const idxNet = header.indexOf('net_credit')
  let cum = 0
  let peak = 0
  const out: TradeRow[] = []
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const cols = line.split(',')
    const date = cols[idxDate] || ''
    const pnlStr = cols[idxPnl] || ''
    const pnl = pnlStr === '' ? 0 : parseFloat(pnlStr)
    cum += isNaN(pnl) ? 0 : pnl
    peak = Math.max(peak, cum)
    const dd = cum - peak
    const netStr = idxNet >= 0 ? cols[idxNet] || '' : ''
    const net = netStr === '' ? null : parseFloat(netStr)
    out.push({
      date,
      symbol: idxSym >= 0 ? cols[idxSym] || '' : '',
      action: idxAction >= 0 ? cols[idxAction] || '' : '',
      kind: idxKind >= 0 ? cols[idxKind] || '' : '',
      net,
      pnl: isNaN(pnl) ? 0 : pnl,
      cum,
      dd,
    })
  }
  return out
}

function computeProfitFactor(trades: TradeRow[]) {
  if (!trades.length) return null
  let wins = 0
  let losses = 0
  trades.forEach((t) => {
    if (t.pnl > 0) wins += t.pnl
    if (t.pnl < 0) losses += t.pnl
  })
  if (losses === 0) return wins > 0 ? Infinity : null
  return wins / Math.abs(losses)
}

function avgPnl(trades: TradeRow[]) {
  if (!trades.length) return null
  const total = trades.reduce((acc, t) => acc + t.pnl, 0)
  return total / trades.length
}

function formatValue(val: any, fmt: MetricCardProps['fmt']) {
  if (val === null || val === undefined || val === '') return '-'
  switch (fmt) {
    case 'currency':
      return typeof val === 'number' ? val.toFixed(2) : val
    case 'percent':
      return typeof val === 'number' ? (val * 100).toFixed(2) + '%' : val
    case 'int':
      return typeof val === 'number' ? Math.round(val) : val
    case 'float':
      return typeof val === 'number' ? val.toFixed(2) : val
    default:
      return val
  }
}
