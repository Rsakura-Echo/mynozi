import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { getSettings, updateSettings, getModelStatus, downloadModel, getDownloadStatus } from '../api';
import { useToast } from './ToastProvider';
import type { AppSettings, ModelStatus } from '../types';

interface DlState {
  status: string;
  message: string;
  current: string;
  total: number;
  done: number;
}

export default function Header() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [models, setModels] = useState<ModelStatus[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showSysMenu, setShowSysMenu] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [saving, setSaving] = useState(false);
  const [dlState, setDlState] = useState<DlState>({ status: 'idle', message: '', current: '', total: 0, done: 0 });
  const dlPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const toast = useToast();

  useEffect(() => {
    getSettings().then(({ data }) => {
      setSettings(data);
      setApiKey(data.runninghub_api_key || '');
      setWorkflowId(data.runninghub_workflow_id || '');
    }).catch(() => {});
  }, []);

  const fetchModels = () => {
    getModelStatus().then(({ data }) => setModels(data.models || [])).catch(() => {});
  };

  useEffect(() => {
    if (showDropdown) fetchModels();
  }, [showDropdown]);

  const startDownload = async (engine: string, modelSize?: string) => {
    try {
      const { data } = await downloadModel(engine, modelSize);
      setDlState(data);
      toast.show(`开始下载 ${engine} 模型...`);
      // Poll for progress
      if (dlPollRef.current) clearInterval(dlPollRef.current);
      dlPollRef.current = setInterval(async () => {
        try {
          const { data: s } = await getDownloadStatus();
          setDlState(s);
          if (s.status === 'done') {
            if (dlPollRef.current) clearInterval(dlPollRef.current);
            toast.show('模型下载完成！');
            fetchModels();
          } else if (s.status === 'error') {
            if (dlPollRef.current) clearInterval(dlPollRef.current);
            toast.show('模型下载失败');
          }
        } catch { /* ignore */ }
      }, 2000);
    } catch (e: any) {
      toast.show(e?.response?.data?.detail || '下载启动失败');
    }
  };

  useEffect(() => {
    return () => { if (dlPollRef.current) clearInterval(dlPollRef.current); };
  }, []);

  const handleSelect = async (value: string) => {
    if (!settings || value === settings.asr_model) {
      setShowDropdown(false);
      return;
    }
    try {
      const { data } = await updateSettings({ asr_model: value });
      setSettings(data);
    } catch {}
    setShowDropdown(false);
  };

  const handleSaveSysConfig = async () => {
    setSaving(true);
    try {
      const { data } = await updateSettings({
        runninghub_api_key: apiKey,
        runninghub_workflow_id: workflowId,
      });
      setSettings(data);
      setApiKey(data.runninghub_api_key || '');
      toast.show('设置已保存');
    } catch {
      toast.show('保存失败');
    }
    setSaving(false);
  };

  return (
    <header style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '24px 0', borderBottom: '1px solid var(--border)', marginBottom: 40,
    }}>
      <Link to="/" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: 'var(--font-display)', fontSize: 28, fontWeight: 500, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          mynozi
        </span>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', background: 'var(--amber)',
          boxShadow: '0 0 12px var(--amber-glow)', animation: 'pulse 2s ease-in-out infinite'
        }} />
      </Link>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>

        {/* ASR Model Selector */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => { setShowDropdown(!showDropdown); setShowSysMenu(false); }}
            title={settings?.asr_model_desc}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
              padding: '8px 14px', borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-secondary)',
              transition: 'all 0.2s',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-light)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}
          >
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', boxShadow: '0 0 6px var(--green)' }} />
            ASR: {settings?.asr_model_label || '加载中...'}
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>▼</span>
          </button>

          {showDropdown && (
            <>
              <div style={{ position: 'fixed', inset: 0, zIndex: 9 }} onClick={() => setShowDropdown(false)} />
              <div style={{
                position: 'absolute', top: '100%', right: 0, marginTop: 8,
                background: 'var(--bg-card)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', padding: 8, minWidth: 360,
                zIndex: 10, boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
                maxHeight: '80vh', overflowY: 'auto',
              }}>
                <div style={{ padding: '8px 14px 6px', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  ASR 引擎
                </div>
                {(settings?.available_models || []).map(m => (
                  <div
                    key={m.value}
                    onClick={() => handleSelect(m.value)}
                    style={{
                      padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                      cursor: 'pointer', transition: 'background 0.15s',
                      background: settings?.asr_model === m.value ? 'rgba(232,153,58,0.08)' : 'transparent',
                      border: settings?.asr_model === m.value ? '1px solid var(--amber-dim)' : '1px solid transparent',
                      marginBottom: 2,
                    }}
                    onMouseEnter={e => { if (settings?.asr_model !== m.value) e.currentTarget.style.background = 'var(--bg-hover)'; }}
                    onMouseLeave={e => { if (settings?.asr_model !== m.value) e.currentTarget.style.background = 'transparent'; }}
                  >
                    <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', marginBottom: 2 }}>
                      {m.label}
                      {settings?.asr_model === m.value && (
                        <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--amber)' }}>✓ 当前</span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {m.desc}
                    </div>
                  </div>
                ))}

                <div style={{ marginTop: 12, padding: '8px 14px 6px', borderTop: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
                    模型缓存
                  </div>

                  {/* Download progress */}
                  {dlState.status === 'downloading' && (
                    <div style={{
                      marginBottom: 10, padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                      background: 'rgba(232,153,58,0.06)', border: '1px solid var(--amber-dim)',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                        <span style={{
                          width: 16, height: 16, borderRadius: '50%', border: '2px solid var(--amber)',
                          borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite',
                        }} />
                        <span style={{ fontSize: 12, color: 'var(--amber)' }}>{dlState.message}</span>
                      </div>
                      {dlState.total > 1 && (
                        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                          {Array.from({ length: dlState.total }).map((_, i) => (
                            <div key={i} style={{
                              flex: 1, height: 3, borderRadius: 2,
                              background: i < dlState.done ? 'var(--green)' : 'var(--border)',
                              transition: 'background 0.3s',
                            }} />
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {dlState.status === 'done' && (
                    <div style={{
                      marginBottom: 10, padding: '8px 14px', borderRadius: 'var(--radius-sm)',
                      background: 'rgba(76,175,146,0.06)', border: '1px solid var(--green)',
                      fontSize: 12, color: 'var(--green)',
                    }}>
                      ✓ {dlState.message}
                    </div>
                  )}

                  {dlState.status === 'error' && (
                    <div style={{
                      marginBottom: 10, padding: '8px 14px', borderRadius: 'var(--radius-sm)',
                      background: 'rgba(208,112,138,0.06)', border: '1px solid var(--red)',
                      fontSize: 12, color: 'var(--red)',
                    }}>
                      ✗ {dlState.message}
                    </div>
                  )}

                  {/* WhisperX models */}
                  <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', marginBottom: 4, marginTop: 8 }}>
                    WhisperX (HuggingFace)
                  </div>
                  {models.length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 0' }}>加载中...</div>
                  ) : (
                    models.map(m => (
                      <div key={m.name} style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        padding: '6px 0', fontSize: 12,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                          <span style={{
                            width: 6, height: 6, borderRadius: '50%',
                            background: m.downloaded ? 'var(--green)' : (m.size_downloaded_gb > 0 ? 'var(--amber)' : 'var(--text-muted)'),
                            boxShadow: m.downloaded ? '0 0 6px var(--green)' : 'none',
                            flexShrink: 0,
                          }} />
                          <span style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.label}</span>
                        </div>
                        <span style={{ color: 'var(--text-muted)', fontSize: 11, flexShrink: 0, marginLeft: 8 }}>
                          {m.downloaded
                            ? `${m.size_gb} GB ✓`
                            : m.size_downloaded_gb > 0
                              ? `${m.size_downloaded_gb}/${m.size_gb} GB`
                              : `${m.size_gb} GB`}
                        </span>
                      </div>
                    ))
                  )}

                  {/* FunASR models */}
                  <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', marginBottom: 4, marginTop: 12 }}>
                    FunASR (ModelScope 国内源)
                  </div>
                  {models.filter(m => m.engine === 'funasr').length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 0' }}>加载中...</div>
                  ) : (
                    models.filter(m => m.engine === 'funasr').map(m => (
                      <div key={m.name} style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        padding: '6px 0', fontSize: 12,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                          <span style={{
                            width: 6, height: 6, borderRadius: '50%',
                            background: m.downloaded ? 'var(--green)' : (m.size_downloaded_gb > 0 ? 'var(--amber)' : 'var(--text-muted)'),
                            boxShadow: m.downloaded ? '0 0 6px var(--green)' : 'none',
                            flexShrink: 0,
                          }} />
                          <span style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.label}</span>
                        </div>
                        <span style={{ color: 'var(--text-muted)', fontSize: 11, flexShrink: 0, marginLeft: 8 }}>
                          {m.downloaded
                            ? `${m.size_gb} GB ✓`
                            : m.size_downloaded_gb > 0
                              ? `${m.size_downloaded_gb}/${m.size_gb} GB`
                              : `~${m.size_gb} GB`}
                        </span>
                      </div>
                    ))
                  )}

                  {/* Download buttons — only show if not all models cached */}
                  {(() => {
                    const currentEngine = settings?.asr_model || 'whisperx';
                    const engineModels = models.filter(m => m.engine === currentEngine);
                    const allCached = engineModels.length > 0 && engineModels.every(m => m.downloaded);
                    const currentSize = settings?.whisper_model_size || 'medium';
                    const currentModelCached = engineModels.find(m => m.name === currentSize)?.downloaded;

                    if (allCached) {
                      return (
                        <div style={{ fontSize: 11, color: 'var(--green)', padding: '8px 0', textAlign: 'center' }}>
                          ✓ 当前引擎模型已全部缓存
                        </div>
                      );
                    }

                    return (
                      <div style={{ display: 'flex', gap: 8, marginTop: 0 }}>
                        {currentEngine === 'whisperx' ? (
                          <button
                            onClick={() => startDownload('whisperx', currentSize)}
                            disabled={dlState.status === 'downloading'}
                            style={{
                              flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border)', background: 'var(--bg-base)',
                              color: 'var(--text-primary)', cursor: dlState.status === 'downloading' ? 'not-allowed' : 'pointer',
                              fontFamily: 'var(--font-ui)', fontSize: 11, opacity: dlState.status === 'downloading' ? 0.5 : 1,
                            }}
                          >{currentModelCached ? `已缓存 ${currentSize}，下载全部` : `下载 WhisperX 模型 (${currentSize})`}</button>
                        ) : (
                          <button
                            onClick={() => startDownload('funasr')}
                            disabled={dlState.status === 'downloading'}
                            style={{
                              flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--amber-dim)', background: 'rgba(232,153,58,0.06)',
                              color: 'var(--amber)', cursor: dlState.status === 'downloading' ? 'not-allowed' : 'pointer',
                              fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 500,
                              opacity: dlState.status === 'downloading' ? 0.5 : 1,
                            }}
                          >下载 FunASR 模型（国内源）</button>
                        )}
                      </div>
                    );
                  })()}

                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.5 }}>
                    首次使用时会自动下载。也可点击上方按钮提前下载，<br />下载后缓存到本地，后续使用无需等待。
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* System Settings */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => { setShowSysMenu(!showSysMenu); setShowDropdown(false); }}
            title="系统设置"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 36, height: 36, borderRadius: 'var(--radius-sm)',
              background: showSysMenu ? 'var(--bg-hover)' : 'transparent',
              border: '1px solid var(--border)',
              cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 16,
              transition: 'all 0.2s',
            }}
            onMouseEnter={e => { if (!showSysMenu) e.currentTarget.style.borderColor = 'var(--border-light)'; }}
            onMouseLeave={e => { if (!showSysMenu) e.currentTarget.style.borderColor = 'var(--border)'; }}
          >
            ⚙
          </button>

          {showSysMenu && (
            <>
              <div style={{ position: 'fixed', inset: 0, zIndex: 9 }} onClick={() => setShowSysMenu(false)} />
              <div style={{
                position: 'absolute', top: '100%', right: 0, marginTop: 8,
                background: 'var(--bg-card)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', padding: 24, width: 400,
                zIndex: 10, boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
              }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 18 }}>
                  RunningHub API 配置
                </div>

                <label style={{ display: 'block', marginBottom: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    API Key
                  </div>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={e => setApiKey(e.target.value)}
                    placeholder="输入 RunningHub API Key..."
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = 'var(--amber)';
                      e.currentTarget.style.boxShadow = '0 0 0 2px rgba(232,153,58,0.15)';
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = 'var(--border)';
                      e.currentTarget.style.boxShadow = 'none';
                    }}
                    style={{
                      width: '100%', background: 'var(--bg-input)', border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)', padding: '10px 14px',
                      fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-primary)',
                      outline: 'none', boxSizing: 'border-box', transition: 'border-color 0.2s, box-shadow 0.2s',
                    }}
                  />
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
                    控制台 → API 调用 中获取，保存后仅显示后 4 位
                  </div>
                </label>

                <label style={{ display: 'block', marginBottom: 20 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    Workflow ID
                  </div>
                  <input
                    type="text"
                    value={workflowId}
                    onChange={e => setWorkflowId(e.target.value)}
                    placeholder="1980237776367083521"
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = 'var(--amber)';
                      e.currentTarget.style.boxShadow = '0 0 0 2px rgba(232,153,58,0.15)';
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = 'var(--border)';
                      e.currentTarget.style.boxShadow = 'none';
                    }}
                    style={{
                      width: '100%', background: 'var(--bg-input)', border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)', padding: '10px 14px',
                      fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-primary)',
                      outline: 'none', boxSizing: 'border-box', transition: 'border-color 0.2s, box-shadow 0.2s',
                    }}
                  />
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
                    工作流页面 URL 末尾的数字，如 runninghub.cn/workflow/<strong>1980237776367083521</strong>
                  </div>
                </label>

                <button
                  onClick={handleSaveSysConfig}
                  disabled={saving}
                  style={{
                    width: '100%', padding: '10px 0', borderRadius: 'var(--radius-sm)',
                    border: 'none', background: 'var(--amber)', color: '#1a1008',
                    fontFamily: 'var(--font-ui)', fontSize: 14, fontWeight: 500,
                    cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.6 : 1,
                    boxShadow: '0 2px 8px rgba(232,153,58,0.2)',
                  }}
                >
                  {saving ? '保存中...' : '保存配置'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      <style>{`@keyframes pulse { 0%,100%{box-shadow:0 0 8px var(--amber-glow)} 50%{box-shadow:0 0 20px var(--amber-glow),0 0 40px rgba(232,153,58,0.3)} }`}</style>
    </header>
  );
}
