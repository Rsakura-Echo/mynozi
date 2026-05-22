import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getProject, uploadFile, updateSentence, softDeleteSentence, splitSentence, addSentenceFromRegion, undoLastAction, generateSentence, generateAll, exportAll, getSentenceAudioUrl, getOriginalAudioUrl } from '../api';
import { useToast } from '../components/ToastProvider';
import WaveformViewer from '../components/WaveformViewer';
import SentenceRow from '../components/SentenceRow';
import type { ProjectDetail, Sentence } from '../types';

export default function ProjectEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const [showBatchConfirm, setShowBatchConfirm] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [playingSid, setPlayingSid] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const sentences = project?.sentences || [];
  const allChecked = sentences.length > 0 && checkedIds.size > 0 && sentences.every(s => checkedIds.has(s.id));

  const handleCheckAll = () => {
    if (allChecked) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(sentences.map(s => s.id)));
    }
  };

  const handleCheckOne = (sid: string, checked: boolean) => {
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (checked) next.add(sid); else next.delete(sid);
      return next;
    });
  };

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const { data } = await getProject(id);
      setProject(data);
    } catch { /* backend not ready */ }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Poll while processing
  useEffect(() => {
    if (!project || project.status !== 'processing') return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [project?.status, load]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !id) return;
    setProject(prev => prev ? { ...prev, status: 'uploading' } : null);
    toast.show('上传中...');
    try {
      await uploadFile(id, file);
      toast.show('上传成功，开始 AI 分析...');
      load();
    } catch {
      toast.show('上传失败');
    }
  };

  const handleReupload = () => {
    // 重置为 uploading 状态以显示上传区域
    setProject(prev => prev ? { ...prev, status: 'uploading' } : null);
  };

  const handleTextChange = async (s: Sentence, text: string) => {
    if (!id) return;
    setProject(prev => prev ? {
      ...prev,
      sentences: prev.sentences.map(x => x.id === s.id ? { ...x, text } : x),
    } : null);
    try {
      await updateSentence(id, s.id, { text });
    } catch { /* ignore */ }
  };

  const handleEmotionChange = async (s: Sentence, key: string, value: number) => {
    if (!id) return;
    setProject(prev => prev ? {
      ...prev,
      sentences: prev.sentences.map(x => x.id === s.id ? { ...x, [`emotion_${key}`]: value } : x),
    } : null);
    try {
      await updateSentence(id, s.id, { [`emotion_${key}`]: value });
    } catch { /* ignore */ }
  };

  const handleGenerate = async (s: Sentence) => {
    if (!id) return;
    setProject(prev => prev ? {
      ...prev,
      sentences: prev.sentences.map(x => x.id === s.id ? { ...x, tts_status: 'generating' as const } : x),
    } : null);
    toast.show(`开始生成：句子 ${s.sort_order + 1}`);
    try {
      await generateSentence(id, s.id);
      const poll = setInterval(async () => {
        const { data: fresh } = await getProject(id);
        setProject(fresh);
        const updated = fresh.sentences.find((x: Sentence) => x.id === s.id);
        if (updated?.tts_status !== 'generating') {
          clearInterval(poll);
          toast.show(updated?.tts_status === 'done' ? `生成完成` : '生成失败');
        }
      }, 2000);
    } catch {
      toast.show('生成失败');
    }
  };

  const handleBatchGenerate = async () => {
    if (!id || !project || checkedIds.size === 0) return;
    setShowBatchConfirm(false);
    const ids = Array.from(checkedIds);
    toast.show(`开始批量生成 ${ids.length} 句（排队中）...`);
    try {
      await generateAll(id, ids);
      setCheckedIds(new Set());
      const poll = setInterval(async () => {
        const { data: fresh } = await getProject(id);
        setProject(fresh);
        if (fresh.sentences.filter((s: Sentence) => ids.includes(s.id)).every((s: Sentence) => s.tts_status !== 'generating')) {
          clearInterval(poll);
          toast.show('批量生成完成！');
        }
      }, 3000);
    } catch {
      toast.show('批量生成失败');
    }
  };

  const handleExportAll = () => {
    if (!id) return;
    window.open(exportAll(id), '_blank');
    toast.show('正在打包导出...');
  };

  const handleDeleteSentence = async (sentenceId: string) => {
    if (!id) return;
    try {
      await softDeleteSentence(id, sentenceId);
      setCheckedIds(prev => { const n = new Set(prev); n.delete(sentenceId); return n; });
      if (selectedSid === sentenceId) setSelectedSid(null);
      toast.show('已删除');
      load();
    } catch {
      toast.show('删除失败');
    }
  };

  const handleUndo = async () => {
    if (!id) return;
    try {
      await undoLastAction(id);
      toast.show('已撤回');
      load();
    } catch {
      toast.show('没有可撤回的操作');
    }
  };

  const handleSplitSentence = async (sentenceId: string, splitTime: number) => {
    if (!id) return;
    try {
      await splitSentence(id, sentenceId, splitTime);
      toast.show('已切割');
      load();
    } catch {
      toast.show('切割失败');
    }
  };

  const handleAddRegion = async (startTime: number, endTime: number) => {
    if (!id) return;
    try {
      toast.show('正在解析框选音频...');
      const { data } = await addSentenceFromRegion(id, startTime, endTime);
      load();
      if (data.asr_used) {
        toast.show('已识别并插入新台词');
      } else {
        toast.show('已插入新台词，请手动编辑文字');
      }
    } catch {
      toast.show('识别失败，该区域可能无有效语音');
    }
  };

  const handlePlayOriginal = (s: Sentence) => {
    if (!id) return;
    if (playingSid === s.id) {
      // 同一句 → 暂停
      audioRef.current?.pause();
      setPlayingSid(null);
      return;
    }
    // 新句子 → 播放
    audioRef.current?.pause();
    const audio = new Audio(getOriginalAudioUrl(id, s.id));
    audio.onended = () => setPlayingSid(null);
    audio.onerror = () => setPlayingSid(null);
    audio.play().catch(() => {});
    audioRef.current = audio;
    setPlayingSid(s.id);
  };

  const handleDownload = (s: Sentence) => {
    if (!id) return;
    window.open(getSentenceAudioUrl(id, s.id), '_blank');
  };

  if (!project) {
    return <div style={{ textAlign: 'center', padding: 80, color: 'var(--text-muted)' }}>加载中...</div>;
  }

  const isReady = project.status === 'ready';

  return (
    <div>
      {/* Top bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '20px 0', borderBottom: '1px solid var(--border)', marginBottom: 24 }}>
        <button onClick={() => navigate('/')} style={{
          background: 'transparent', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontFamily: 'var(--font-ui)', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6,
        }}>
          ← 返回
        </button>
        <span style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 500, flex: 1 }}>{project.name}</span>

        {/* Re-upload button (always visible when ready or error) */}
        {(isReady || project.status === 'error') && (
          <button onClick={handleReupload} style={{
            fontFamily: 'var(--font-ui)', fontSize: 12, padding: '6px 14px', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}>重新上传</button>
        )}

        <button onClick={handleUndo} disabled={!isReady} title="撤回上一步操作" style={{
          fontFamily: 'var(--font-ui)', fontSize: 12, padding: '6px 14px', borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-secondary)',
          cursor: isReady ? 'pointer' : 'not-allowed', opacity: isReady ? 1 : 0.4,
        }}>↩ 撤回</button>
        <button onClick={() => setShowBatchConfirm(true)} disabled={!isReady} style={{
          fontFamily: 'var(--font-ui)', fontSize: 12, padding: '6px 14px', borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)',
          cursor: isReady ? 'pointer' : 'not-allowed', opacity: isReady ? 1 : 0.4,
        }}>批量生成</button>
        <button onClick={handleExportAll} disabled={!isReady} style={{
          fontFamily: 'var(--font-ui)', fontSize: 12, padding: '6px 14px', borderRadius: 'var(--radius-sm)',
          border: 'none', background: 'var(--amber)', color: '#1a1008', cursor: isReady ? 'pointer' : 'not-allowed',
          opacity: isReady ? 1 : 0.4, boxShadow: '0 2px 8px rgba(232,153,58,0.2)',
        }}>导出全部</button>
      </div>

      {/* Upload zone (when new, uploading, or user clicked re-upload) */}
      {(project.status === 'uploading' || project.status === 'error') && (
        <label style={{
          display: 'block', border: '2px dashed var(--border)', borderRadius: 'var(--radius)',
          padding: '40px 24px', textAlign: 'center', cursor: 'pointer',
          background: 'var(--bg-card)', marginBottom: 24, transition: 'all 0.3s',
        }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>🎙️</div>
          <h4 style={{ fontSize: 15, marginBottom: 4 }}>
            {project.status === 'error' ? '处理出错，点击重新上传' : '上传音视频文件'}
          </h4>
          {project.last_error && (
            <p style={{ fontSize: 11, color: 'var(--red)', marginBottom: 8, fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
              {project.last_error}
            </p>
          )}
          <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>支持 MP4、MOV、WAV、MP3 等格式 · 最大 2GB</p>
          <input type="file" accept="audio/*,video/*" onChange={handleUpload} style={{ display: 'none' }} />
        </label>
      )}

      {project.status === 'processing' && (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>⏳</div>
          <h4>正在分析音频...</h4>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 24 }}>WhisperX 语音识别 + 说话人分离</p>
          <div style={{ height: 3, background: 'var(--border)', borderRadius: 2, width: 300, margin: '0 auto', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: '65%', background: 'var(--amber)', borderRadius: 2, transition: 'width 0.5s' }} />
          </div>
        </div>
      )}

      {/* Waveform + sentences */}
      {isReady && (
        <>
          <WaveformViewer
            sentences={project.sentences}
            selectedSid={selectedSid}
            projectId={id}
            onDeleteSentence={handleDeleteSentence}
            onSplitSentence={handleSplitSentence}
            onAddRegion={handleAddRegion}
          />

          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', overflow: 'hidden', marginTop: 20,
          }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '32px 40px 120px 1fr 2fr 1fr 150px',
              padding: '14px 20px', gap: 12, background: 'var(--bg-base)',
              borderBottom: '1px solid var(--border)', fontSize: 11, fontWeight: 500,
              color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em',
              alignItems: 'center',
            }}>
              <span style={{ textAlign: 'center' }}>
                <input type="checkbox" checked={allChecked} onChange={handleCheckAll}
                  style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--amber)' }} />
              </span>
              <span>#</span><span>说话人</span><span>原文</span><span>配音文本</span><span>情感</span><span>操作</span>
            </div>
            {sentences.map((s, i) => (
              <SentenceRow
                key={s.id}
                sentence={s}
                index={i}
                isSelected={selectedSid === s.id}
                checked={checkedIds.has(s.id)}
                onSelect={() => setSelectedSid(s.id)}
                onCheckChange={checked => handleCheckOne(s.id, checked)}
                onTextChange={t => handleTextChange(s, t)}
                onEmotionChange={(k, v) => handleEmotionChange(s, k, v)}
                onGenerate={() => handleGenerate(s)}
                onDownload={() => handleDownload(s)}
                onPlayOriginal={() => handlePlayOriginal(s)}
                isPlaying={playingSid === s.id}
              />
            ))}
          </div>
        </>
      )}

      {/* Batch confirm modal */}
      {showBatchConfirm && (
        <div onClick={() => setShowBatchConfirm(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 50,
          display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(4px)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)', padding: 32, width: 420, maxWidth: '90vw',
          }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, marginBottom: 16 }}>确认批量生成</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6, marginBottom: 20 }}>
              即将对 <strong style={{ color: 'var(--amber)' }}>{checkedIds.size}</strong> 条句子进行配音生成。<br />
              RunningHub 免费会员一次只能处理一句，将排队逐句执行。
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowBatchConfirm(false)} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>我再看看</button>
              <button onClick={handleBatchGenerate} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--amber)', color: '#1a1008', cursor: 'pointer',
                boxShadow: '0 2px 8px rgba(232,153,58,0.2)',
              }}>确认生成</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
