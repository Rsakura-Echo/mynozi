import { useState } from 'react';
import type { Sentence } from '../types';
import { EMOTION_LABELS, EMOTION_KEYS } from '../types';


const SPEAKER_DOTS = ['var(--amber)', 'var(--blue)', 'var(--violet)', 'var(--green)', 'var(--red)'];
const speakerDotCache: Record<string, string> = {};

function getSpeakerDot(sid: string): string {
  if (!speakerDotCache[sid]) {
    const idx = Object.keys(speakerDotCache).length % SPEAKER_DOTS.length;
    speakerDotCache[sid] = SPEAKER_DOTS[idx];
  }
  return speakerDotCache[sid];
}

interface Props {
  sentence: Sentence;
  index: number;
  isSelected: boolean;
  checked: boolean;
  onSelect: () => void;
  onCheckChange: (checked: boolean) => void;
  onTextChange: (text: string) => void;
  onEmotionChange: (key: string, value: number) => void;
  onGenerate: () => void;
  onDownload: () => void;
  onPlayOriginal: () => void;
  isPlaying: boolean;
}

export default function SentenceRow({
  sentence: s, index, isSelected, checked, onSelect, onCheckChange,
  onTextChange, onEmotionChange, onGenerate, onDownload, onPlayOriginal, isPlaying,
}: Props) {
  const [showEmotions, setShowEmotions] = useState(false);

  const statusColors: Record<string, string> = {
    pending: 'var(--border)',
    generating: 'var(--blue)',
    done: 'var(--green)',
    failed: 'var(--red)',
  };
  const statusTitles: Record<string, string> = {
    pending: '待生成', generating: '生成中...', done: '已完成', failed: '失败',
  };

  return (
    <>
      <div onClick={onSelect} style={{
        display: 'grid', gridTemplateColumns: '32px 40px 120px 1fr 2fr 1fr 150px',
        padding: '12px 20px', gap: 12, alignItems: 'center',
        borderBottom: '1px solid var(--border)',
        background: isSelected ? 'rgba(232,153,58,0.04)' : 'transparent',
        borderLeft: isSelected ? '2px solid var(--amber)' : '2px solid transparent',
        cursor: 'pointer', transition: 'background 0.15s', minHeight: 56,
      }}>
        {/* Checkbox */}
        <span style={{ textAlign: 'center' }}>
          <input
            type="checkbox"
            checked={checked}
            onChange={e => onCheckChange(e.target.checked)}
            onClick={e => e.stopPropagation()}
            style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--amber)' }}
          />
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', textAlign: 'center' }}>
          {index + 1}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 20, fontSize: 12, fontWeight: 500, background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: getSpeakerDot(s.speaker_id || ''), flexShrink: 0 }} />
          {s.speaker_name || '未知'}
        </span>
        <span style={{ fontSize: 13, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={s.text}>
          {s.text}
        </span>
        <input
          value={s.text}
          onChange={e => onTextChange(e.target.value)}
          onClick={e => e.stopPropagation()}
          style={{
            width: '100%', background: 'var(--bg-input)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)', padding: '8px 12px',
            fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-primary)',
            outline: 'none',
          }}
          onFocus={e => (e.target.style.borderColor = 'var(--amber-dim)')}
          onBlur={e => (e.target.style.borderColor = 'var(--border)')}
        />
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {EMOTION_KEYS.map(k => (
              <span key={k} style={{
                width: 6, height: 6, borderRadius: '50%',
                background: (s as any)[`emotion_${k}`] > 0 ? 'var(--amber)' : 'var(--border)',
              }} />
            ))}
          </span>
          <span onClick={e => { e.stopPropagation(); setShowEmotions(!showEmotions); }}
            style={{ fontSize: 10, color: 'var(--text-muted)', cursor: 'pointer' }}>
            ⚙
          </span>
        </span>
        <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <button title="播放原音" onClick={e => { e.stopPropagation(); onPlayOriginal(); }}
            style={{ width: 34, height: 34, borderRadius: '50%', border: '1px solid var(--border)', background: isPlaying ? 'rgba(232,153,58,0.12)' : 'var(--bg-card)', color: isPlaying ? 'var(--amber)' : 'var(--text-secondary)', cursor: 'pointer', fontSize: 14, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
            {isPlaying ? '⏸' : '▶'}
          </button>
          {s.tts_status === 'generating' ? (
            <button disabled style={{ width: 34, height: 34, borderRadius: '50%', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-muted)', cursor: 'not-allowed', fontSize: 14, opacity: 0.4, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              ⏳
            </button>
          ) : (
            <button title="生成配音" onClick={e => { e.stopPropagation(); onGenerate(); }}
              style={{ width: 34, height: 34, borderRadius: '50%', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 14, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              🎤
            </button>
          )}
          {s.tts_status === 'done' ? (
            <button title="下载配音" onClick={e => { e.stopPropagation(); onDownload(); }}
              style={{ width: 34, height: 34, borderRadius: '50%', border: '1px solid var(--amber-dim)', background: 'rgba(232,153,58,0.08)', color: 'var(--amber)', cursor: 'pointer', fontSize: 14, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              ⬇
            </button>
          ) : (
            <button disabled style={{ width: 34, height: 34, borderRadius: '50%', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-muted)', cursor: 'not-allowed', fontSize: 14, opacity: 0.25, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              ⬇
            </button>
          )}
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: statusColors[s.tts_status] || 'var(--border)',
            animation: s.tts_status === 'generating' ? 'pulse-status 1s ease-in-out infinite' : 'none',
          }} title={statusTitles[s.tts_status]} />
        </span>
      </div>

      {/* Emotion panel */}
      {showEmotions && (
        <div onClick={e => e.stopPropagation()} style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border)',
          background: 'var(--bg-base)', display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 14,
        }}>
          {EMOTION_KEYS.map(k => (
            <div key={k}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                <span>{EMOTION_LABELS[k]}</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--amber)' }}>{(s as any)[`emotion_${k}`]}</span>
              </div>
              <input
                type="range" min={0} max={100} value={(s as any)[`emotion_${k}`]}
                onChange={e => onEmotionChange(k, parseInt(e.target.value))}
                style={{
                  WebkitAppearance: 'none', width: '100%', height: 4,
                  borderRadius: 2, background: 'var(--border)', outline: 'none',
                }}
              />
            </div>
          ))}
        </div>
      )}

      <style>{`@keyframes pulse-status { 0%,100%{opacity:1} 50%{opacity:0.4} }`}</style>
    </>
  );
}
