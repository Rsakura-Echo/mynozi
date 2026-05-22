import { useRef, useEffect, useState, useCallback } from 'react';
import type { Sentence } from '../types';
import { getWaveformData } from '../api';

const SPEAKER_COLORS = [
  'rgba(232,153,58,0.5)',
  'rgba(91,155,213,0.5)',
  'rgba(139,126,200,0.5)',
  'rgba(76,175,146,0.5)',
  'rgba(208,112,138,0.5)',
];

interface Props {
  sentences: Sentence[];
  selectedSid: string | null;
  projectId?: string;
  onDeleteSentence: (sentenceId: string) => void;
  onSplitSentence: (sentenceId: string, splitTime: number) => Promise<void>;
  onAddRegion: (startTime: number, endTime: number) => Promise<void>;
}

/** 后备确定性伪随机（波形数据未加载时使用） */
function pseudoAmp(index: number): number {
  const x = Math.sin(index * 127.1 + 311.7) * 43758.5453;
  return 0.3 + (x - Math.floor(x)) * 0.5;
}

export default function WaveformViewer({
  sentences, selectedSid, projectId,
  onDeleteSentence, onSplitSentence, onAddRegion,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, hasDragged: false, startTime: 0, endTime: 0 });
  const [hoveredSentence, setHoveredSentence] = useState<Sentence | null>(null);
  const [cursorX, setCursorX] = useState(0);
  const [cursorTime, setCursorTime] = useState(0);
  const [showSplitConfirm, setShowSplitConfirm] = useState(false);
  const [splitTarget, setSplitTarget] = useState<{ sid: string; time: number } | null>(null);
  const [splitting, setSplitting] = useState(false);
  const [peaks, setPeaks] = useState<number[]>([]);
  // 拖拽框选状态
  const [isDragging, setIsDragging] = useState(false);
  const [dragStartX, setDragStartX] = useState(0);
  const [dragEndX, setDragEndX] = useState(0);
  const [dragStartTime, setDragStartTime] = useState(0);
  const [dragEndTime, setDragEndTime] = useState(0);
  const [hasDragged, setHasDragged] = useState(false);
  const [showRegionConfirm, setShowRegionConfirm] = useState(false);
  const [regionTarget, setRegionTarget] = useState<{ start: number; end: number } | null>(null);
  const [regionLoading, setRegionLoading] = useState(false);

  const DRAG_MIN_PX = 5;
  // 像素/秒：控制波形水平拉伸比例
  const PX_PER_SECOND = 80;
  const MIN_CANVAS_WIDTH = 800;

  const totalDur = sentences.length > 0 ? sentences[sentences.length - 1].end_time : 0;
  const canvasW = Math.max(MIN_CANVAS_WIDTH, totalDur * PX_PER_SECOND);

  // 全局 mouseup：处理拖拽到容器外松开的情况
  useEffect(() => {
    const handleWindowMouseUp = () => {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      setIsDragging(false);
      if (!dragRef.current.hasDragged) return;
      let start = dragRef.current.startTime;
      let end = dragRef.current.endTime;
      if (start > end) [start, end] = [end, start];
      if (end - start >= 0.2) {
        setRegionTarget({ start, end });
        setShowRegionConfirm(true);
      }
    };
    window.addEventListener('mouseup', handleWindowMouseUp);
    return () => window.removeEventListener('mouseup', handleWindowMouseUp);
  }, []);

  // 加载真实波形数据
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    getWaveformData(projectId).then(({ data }) => {
      if (!cancelled && data.peaks) setPeaks(data.peaks);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);

  const getSentenceAtX = useCallback((x: number): Sentence | null => {
    if (!containerRef.current || sentences.length === 0) return null;
    const rect = containerRef.current.getBoundingClientRect();
    const relX = x - rect.left + containerRef.current.scrollLeft;
    const time = (relX / canvasW) * totalDur;
    return sentences.find(s => time >= s.start_time && time <= s.end_time) || null;
  }, [sentences, totalDur, canvasW]);

  const getTimeAtX = useCallback((x: number): number => {
    if (!containerRef.current) return 0;
    const rect = containerRef.current.getBoundingClientRect();
    return ((x - rect.left + containerRef.current.scrollLeft) / canvasW) * totalDur;
  }, [totalDur, canvasW]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return;
    // 右键 / 非左键 忽略
    if (e.button !== 0) return;
    // 点击在句子上 → 不拖拽（留给 click 处理切割）
    if (getSentenceAtX(e.clientX)) return;

    const rect = containerRef.current.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const time = getTimeAtX(e.clientX);
    setIsDragging(true);
    setHasDragged(false);
    setDragStartX(localX);
    setDragEndX(localX);
    setDragStartTime(time);
    setDragEndTime(time);
    dragRef.current = { active: true, hasDragged: false, startTime: time, endTime: time };
  }, [getTimeAtX, getSentenceAtX]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return;
    if (isDragging) {
      const rect = containerRef.current.getBoundingClientRect();
      const localX = e.clientX - rect.left;
      const endTime = getTimeAtX(e.clientX);
      setDragEndX(localX);
      setDragEndTime(endTime);
      dragRef.current.endTime = endTime;
      if (Math.abs(localX - dragStartX) >= DRAG_MIN_PX) {
        setHasDragged(true);
        dragRef.current.hasDragged = true;
      }
    } else {
      // 非拖拽时的 hover 逻辑
      const s = getSentenceAtX(e.clientX);
      setHoveredSentence(s);
      setCursorX(e.clientX);
      setCursorTime(getTimeAtX(e.clientX));
    }
  }, [isDragging, dragStartX, getSentenceAtX, getTimeAtX]);

  const handleMouseUp = useCallback(() => {
    if (!isDragging) return;
    dragRef.current.active = false;
    setIsDragging(false);

    if (!hasDragged || !containerRef.current) return;

    // 计算框选的时间范围（确保 start < end）
    let start = dragStartTime;
    let end = dragEndTime;
    if (start > end) [start, end] = [end, start];
    // 最短 0.2 秒
    if (end - start < 0.2) return;

    setRegionTarget({ start, end });
    setShowRegionConfirm(true);
  }, [isDragging, hasDragged, dragStartTime, dragEndTime]);

  const handleMouseLeave = useCallback(() => {
    setHoveredSentence(null);
    setIsDragging(false);
    dragRef.current.active = false;
  }, []);

  // ── Canvas drawing ──
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || sentences.length === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const h = 160;
    canvas.width = canvasW * dpr;
    canvas.height = h * dpr;
    canvas.style.width = canvasW + 'px';
    canvas.style.height = h + 'px';

    const ctx = canvas.getContext('2d')!;
    ctx.scale(dpr, dpr);
    const w = canvasW;

    ctx.clearRect(0, 0, w, h);

    // Speaker-color mapping
    const speakerMap: Record<string, number> = {};
    let colorIdx = 0;
    sentences.forEach(s => {
      if (s.speaker_id && !(s.speaker_id in speakerMap)) {
        speakerMap[s.speaker_id] = colorIdx++ % SPEAKER_COLORS.length;
      }
    });

    const subtitleH = 48;
    const waveTop = subtitleH + 4;
    const waveH = h - waveTop - 20;
    const mid = waveTop + waveH / 2;

    // ── 1. Subtitle track ──
    ctx.font = '11px "DM Sans", "PingFang SC", sans-serif';
    ctx.textBaseline = 'top';

    const tickInterval = Math.max(5, Math.ceil(totalDur / 10));
    for (let t = 0; t <= totalDur; t += tickInterval) {
      const x = (t / totalDur) * w;
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();

      const min = Math.floor(t / 60);
      const sec = Math.floor(t % 60);
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.fillText(`${min}:${String(sec).padStart(2, '0')}`, x + 3, 2);
    }

    // Sentence text labels
    const labelMinWidth = 40;
    sentences.forEach((s, i) => {
      const x1 = (s.start_time / totalDur) * w;
      const x2 = (s.end_time / totalDur) * w;
      const segW = x2 - x1;

      const ci = s.speaker_id ? speakerMap[s.speaker_id] : 0;
      ctx.fillStyle = SPEAKER_COLORS[ci % SPEAKER_COLORS.length].replace('0.5', '0.12');
      ctx.fillRect(x1, 0, segW, subtitleH);

      ctx.strokeStyle = SPEAKER_COLORS[ci % SPEAKER_COLORS.length].replace('0.5', '0.4');
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x1, 0);
      ctx.lineTo(x1, subtitleH);
      ctx.stroke();

      if (segW > labelMinWidth) {
        const maxChars = Math.max(2, Math.floor(segW / 7.5));
        let label = s.text;
        if (label.length > maxChars) label = label.substring(0, maxChars - 1) + '…';
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.fillText(label, x1 + 4, 4);
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.font = '9px "DM Sans", sans-serif';
        ctx.fillText(`#${i + 1}`, x1 + 4, subtitleH - 16);
        ctx.font = '11px "DM Sans", "PingFang SC", sans-serif';
      }
    });

    // ── 2. Waveform bars ──
    const useReal = peaks.length > 0;
    const BAR_PX = 2.5;  // 每根柱子固定像素宽度
    const barCount = Math.floor(w / BAR_PX);
    const barW = BAR_PX;

    for (let i = 0; i < barCount; i++) {
      const t = (i / barCount) * totalDur;
      const s = sentences.find(x => t >= x.start_time && t <= x.end_time);
      let amp: number;
      if (useReal) {
        // 从 peaks 数据中采样对应位置
        const peakIdx = Math.floor((i / barCount) * peaks.length);
        amp = peaks[peakIdx] || 0;
      } else {
        amp = pseudoAmp(i);
      }
      const barH = Math.max(1, amp * waveH * 0.7);
      const x = i * barW;

      if (s && s.speaker_id) {
        ctx.fillStyle = SPEAKER_COLORS[speakerMap[s.speaker_id] % SPEAKER_COLORS.length];
      } else {
        ctx.fillStyle = 'rgba(100,100,120,0.3)';
      }
      ctx.fillRect(x, mid - barH / 2, barW - 0.5, barH);
    }

    // ── 3. Sentence dividers ──
    sentences.forEach(s => {
      const x1 = (s.start_time / totalDur) * w;
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x1, waveTop);
      ctx.lineTo(x1, waveTop + waveH);
      ctx.stroke();
      // Top triangle marker
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.beginPath();
      ctx.moveTo(x1 - 4, waveTop);
      ctx.lineTo(x1 + 4, waveTop);
      ctx.lineTo(x1, waveTop + 6);
      ctx.fill();
    });

    // ── 4. Speaker bar ──
    const speakerRegions: { start: number; end: number; name: string; color: string }[] = [];
    let curSpeaker = sentences[0]?.speaker_id;
    let regionStart = 0;
    sentences.forEach(s => {
      if (s.speaker_id !== curSpeaker) {
        speakerRegions.push({
          start: regionStart / totalDur,
          end: s.start_time / totalDur,
          name: s.speaker_name || '?',
          color: SPEAKER_COLORS[curSpeaker ? speakerMap[curSpeaker] % SPEAKER_COLORS.length : 0],
        });
        curSpeaker = s.speaker_id;
        regionStart = s.start_time;
      }
    });
    if (curSpeaker) {
      speakerRegions.push({
        start: regionStart / totalDur,
        end: 1,
        name: sentences[sentences.length - 1].speaker_name || '?',
        color: SPEAKER_COLORS[curSpeaker ? speakerMap[curSpeaker] % SPEAKER_COLORS.length : 0],
      });
    }

    const barY = waveTop + waveH + 2;
    speakerRegions.forEach(r => {
      ctx.fillStyle = r.color;
      ctx.fillRect(r.start * w, barY, (r.end - r.start) * w, 3);
      if ((r.end - r.start) * w > 40) {
        ctx.fillStyle = r.color;
        ctx.font = '10px "DM Sans", sans-serif';
        ctx.fillText(r.name, r.start * w + 4, barY - 12);
      }
    });

  }, [sentences, totalDur, peaks]);

  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: 24, minWidth: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>音频波形 + 字幕</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
          {sentences.length > 0 ? `总时长 ${formatTime(totalDur)} · ${sentences.length} 句` : ''}
        </span>
      </div>
      <div style={{ overflow: 'auto', width: '100%', borderRadius: 'var(--radius-sm)' }}>
      <div
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onClick={(e) => {
          if (hasDragged) return;
          const s = getSentenceAtX(e.clientX);
          if (!s) return;
          const time = getTimeAtX(e.clientX);
          if (time > s.start_time + 0.1 && time < s.end_time - 0.1) {
            setSplitTarget({ sid: s.id, time });
            setShowSplitConfirm(true);
          }
        }}
        style={{
          position: 'relative', height: 160, borderRadius: 'var(--radius-sm)',
          overflow: 'auto hidden', userSelect: 'none',
          cursor: isDragging ? 'col-resize'
            : hoveredSentence ? 'col-resize'
            : 'crosshair',
        }}
      >
        <canvas ref={canvasRef} style={{ pointerEvents: 'none', display: 'block' }} />

        {selectedSid && sentences.find(s => s.id === selectedSid) && (
          <div style={{
            position: 'absolute', top: 0, bottom: 0, width: 2,
            background: 'var(--amber)', boxShadow: '0 0 8px var(--amber-glow)',
            left: `${(sentences.find(s => s.id === selectedSid)!.start_time / totalDur) * 100}%`,
            pointerEvents: 'none', zIndex: 2,
          }} />
        )}

        {hoveredSentence && (() => {
          const scrollLeft = containerRef.current?.scrollLeft || 0;
          const contLeft = containerRef.current?.getBoundingClientRect().left || 0;
          const hoverRight = (hoveredSentence.end_time / totalDur) * canvasW;
          return (
            <>
              <div style={{
                position: 'absolute', top: 0, bottom: 0, width: 1,
                background: 'rgba(255,255,255,0.5)',
                left: cursorX - contLeft + scrollLeft,
                pointerEvents: 'none', zIndex: 2,
              }} />
              <div style={{
                position: 'absolute', bottom: 4,
                left: cursorX - contLeft + scrollLeft + 10,
                background: 'rgba(0,0,0,0.75)', color: '#fff', fontSize: 10,
                padding: '2px 6px', borderRadius: 4, pointerEvents: 'none', zIndex: 3,
                fontFamily: 'var(--font-mono)',
              }}>
                {formatTime(cursorTime)}
              </div>
              <div style={{
                position: 'absolute', top: 4, left: hoverRight - 24,
                zIndex: 3, display: 'flex', gap: 3,
              }}>
                <button onClick={e => { e.stopPropagation(); onDeleteSentence(hoveredSentence.id); setHoveredSentence(null); }}
                  title="删除此分段" style={{
                    width: 20, height: 20, borderRadius: '50%',
                    background: 'rgba(208,112,138,0.85)', color: '#fff',
                    border: 'none', cursor: 'pointer', fontSize: 14,
                    lineHeight: '20px', textAlign: 'center', padding: 0,
                  }}>×</button>
              </div>
            </>
          );
        })()}

        {showSplitConfirm && splitTarget && (
          <div style={{
            position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', padding: '16px 20px', zIndex: 10,
            boxShadow: '0 8px 30px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>
              在 <strong style={{ color: 'var(--amber)', fontFamily: 'var(--font-mono)' }}>{formatTime(splitTarget.time)}</strong> 处切割此句？
            </span>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={e => { e.stopPropagation(); setShowSplitConfirm(false); setSplitTarget(null); }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 12, padding: '5px 14px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>取消</button>
              <button onClick={async e => {
                e.stopPropagation();
                setSplitting(true);
                try {
                  await onSplitSentence(splitTarget.sid, splitTarget.time);
                } finally {
                  setSplitting(false);
                  setShowSplitConfirm(false);
                  setSplitTarget(null);
                }
              }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 12, padding: '5px 14px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--amber)', color: '#1a1008', cursor: 'pointer',
              }}>确认切割</button>
            </div>
          </div>
        )}

        {/* 拖拽框选矩形 */}
        {isDragging && hasDragged && (() => {
          const left = Math.min(dragStartX, dragEndX);
          const width = Math.abs(dragEndX - dragStartX);
          return (
            <div style={{
              position: 'absolute', top: 48, bottom: 20, left, width,
              background: 'rgba(91,155,213,0.18)',
              border: '1.5px solid rgba(91,155,213,0.5)',
              pointerEvents: 'none', zIndex: 4, borderRadius: 2,
            }}>
              <span style={{
                position: 'absolute', bottom: 4, left: '50%', transform: 'translateX(-50%)',
                background: 'rgba(0,0,0,0.8)', color: '#fff', fontSize: 10,
                padding: '2px 8px', borderRadius: 4, whiteSpace: 'nowrap',
                fontFamily: 'var(--font-mono)',
              }}>
                {formatTime(Math.min(dragStartTime, dragEndTime))} → {formatTime(Math.max(dragStartTime, dragEndTime))}
              </span>
            </div>
          );
        })()}

        {/* 框选确认弹窗 */}
        {showRegionConfirm && regionTarget && (
          <div style={{
            position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', padding: '16px 20px', zIndex: 10,
            boxShadow: '0 8px 30px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>
              识别框选区域 <strong style={{ color: 'var(--blue)', fontFamily: 'var(--font-mono)' }}>{formatTime(regionTarget.start)} → {formatTime(regionTarget.end)}</strong> ？
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              将对该音频片段进行语音识别，并插入到台词列表。
            </span>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={e => { e.stopPropagation(); setShowRegionConfirm(false); setRegionTarget(null); }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 12, padding: '5px 14px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>取消</button>
              <button onClick={async e => {
                e.stopPropagation();
                setRegionLoading(true);
                try {
                  await onAddRegion(regionTarget.start, regionTarget.end);
                } finally {
                  setRegionLoading(false);
                  setShowRegionConfirm(false);
                  setRegionTarget(null);
                }
              }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 12, padding: '5px 14px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--blue)', color: '#fff', cursor: 'pointer',
              }}>开始解析</button>
            </div>
          </div>
        )}

        {splitting && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 10,
            background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(2px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 12,
          }}>
            <div style={{
              width: 32, height: 32, borderRadius: '50%',
              border: '3px solid rgba(232,153,58,0.2)',
              borderTopColor: 'var(--amber)',
              animation: 'spin 0.8s linear infinite',
            }} />
            <span style={{ color: 'var(--amber)', fontSize: 13, fontFamily: 'var(--font-ui)' }}>
              正在识别切割文字...
            </span>
          </div>
        )}

        {regionLoading && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 10,
            background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(2px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 12,
          }}>
            <div style={{
              width: 32, height: 32, borderRadius: '50%',
              border: '3px solid rgba(91,155,213,0.2)',
              borderTopColor: 'var(--blue)',
              animation: 'spin 0.8s linear infinite',
            }} />
            <span style={{ color: 'var(--blue)', fontSize: 13, fontFamily: 'var(--font-ui)' }}>
              正在解析框选音频...
            </span>
          </div>
        )}

      </div>
      </div>
    </div>
  );
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(Math.floor((sec % 1) * 10))}`;
}
