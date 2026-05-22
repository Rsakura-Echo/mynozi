import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listProjects, createProject, renameProject, deleteProject } from '../api';
import { useToast } from '../components/ToastProvider';
import type { Project } from '../types';

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [renameTarget, setRenameTarget] = useState<Project | null>(null);
  const [renameName, setRenameName] = useState('');
  const navigate = useNavigate();
  const toast = useToast();

  const load = async () => {
    try {
      const { data } = await listProjects();
      setProjects(data);
    } catch { /* backend not ready yet */ }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!name.trim()) return;
    try {
      const { data } = await createProject(name.trim());
      setShowModal(false);
      setName('');
      toast.show(`项目 "${name.trim()}" 已创建`);
      navigate(`/project/${data.id}`);
    } catch {
      toast.show('创建失败，请检查后端是否启动');
    }
  };

  const handleRename = async () => {
    if (!renameTarget || !renameName.trim()) return;
    try {
      await renameProject(renameTarget.id, renameName.trim());
      toast.show(`已重命名为 "${renameName.trim()}"`);
      setRenameTarget(null);
      load();
    } catch {
      toast.show('重命名失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteProject(deleteTarget.id);
      toast.show(`项目 "${deleteTarget.name}" 已删除`);
      setDeleteTarget(null);
      load();
    } catch {
      toast.show('删除失败');
    }
  };

  return (
    <div>
      <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 32, fontWeight: 500, marginBottom: 4 }}>
        我的配音项目
      </h1>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 32 }}>
        上传音视频，智能切句，逐句重新配音
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
        {/* New project card */}
        <div onClick={() => setShowModal(true)} style={{
          background: 'transparent', border: '2px dashed var(--border)',
          borderRadius: 'var(--radius)', padding: '48px 24px',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', gap: 12, cursor: 'pointer',
          minHeight: 200, transition: 'all 0.3s',
        }}
          onMouseEnter={e => { (e.currentTarget.style.borderColor = 'var(--amber-dim)'); (e.currentTarget.style.background = 'rgba(232,153,58,0.03)'); }}
          onMouseLeave={e => { (e.currentTarget.style.borderColor = 'var(--border)'); (e.currentTarget.style.background = 'transparent'); }}
        >
          <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--bg-card)', border: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, color: 'var(--text-muted)' }}>
            +
          </div>
          <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>创建新项目</span>
        </div>

        {projects.map(p => (
          <div key={p.id} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', padding: 24, cursor: 'pointer',
            transition: 'all 0.25s', position: 'relative', overflow: 'hidden',
          }}
            onMouseEnter={e => {
              e.currentTarget.style.borderColor = 'var(--border-light)';
              e.currentTarget.style.background = 'var(--bg-hover)';
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = '0 8px 30px rgba(0,0,0,0.3)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderColor = 'var(--border)';
              e.currentTarget.style.background = 'var(--bg-card)';
              e.currentTarget.style.transform = 'none';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <div onClick={() => navigate(`/project/${p.id}`)}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 500 }}>{p.name}</span>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {new Date(p.created_at).toLocaleDateString('zh-CN')}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-muted)' }}>
                <span>{p.duration ? `${Math.floor(p.duration / 60)}:${String(Math.floor(p.duration % 60)).padStart(2, '0')}` : '--:--'}</span>
                <span className={`badge badge-${p.status}`} style={{
                  fontSize: 11, fontWeight: 500, padding: '3px 10px', borderRadius: 20,
                  background: p.status === 'ready' ? 'rgba(76,175,146,0.12)' : p.status === 'processing' ? 'rgba(91,155,213,0.12)' : 'rgba(92,92,110,0.2)',
                  color: p.status === 'ready' ? 'var(--green)' : p.status === 'processing' ? 'var(--blue)' : 'var(--text-muted)',
                }}>
                  {p.status === 'ready' ? '就绪' : p.status === 'processing' ? '处理中' : p.status === 'error' ? '出错' : '待上传'}
                </span>
              </div>
            </div>

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: 8, marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <button onClick={e => { e.stopPropagation(); setRenameTarget(p); setRenameName(p.name); }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 11, padding: '4px 10px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
              }}>重命名</button>
              <button onClick={e => { e.stopPropagation(); setDeleteTarget(p); }} style={{
                fontFamily: 'var(--font-ui)', fontSize: 11, padding: '4px 10px',
                borderRadius: 'var(--radius-sm)', border: '1px solid rgba(208,112,138,0.3)',
                background: 'transparent', color: 'var(--red)', cursor: 'pointer',
              }}>删除</button>
            </div>
          </div>
        ))}
      </div>

      {/* Create modal */}
      {showModal && (
        <div onClick={() => setShowModal(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center',
          backdropFilter: 'blur(4px)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)', padding: 32, width: 420, maxWidth: '90vw',
          }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, marginBottom: 20 }}>创建新项目</h3>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="输入项目名称..." autoFocus
              style={{
                width: '100%', background: 'var(--bg-input)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)', padding: '12px 16px',
                fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--text-primary)',
                outline: 'none', marginBottom: 20,
              }}
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowModal(false)} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>取消</button>
              <button onClick={handleCreate} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--amber)', color: '#1a1008', cursor: 'pointer',
                boxShadow: '0 2px 8px rgba(232,153,58,0.2)',
              }}>创建</button>
            </div>
          </div>
        </div>
      )}

      {/* Rename modal */}
      {renameTarget && (
        <div onClick={() => setRenameTarget(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center',
          backdropFilter: 'blur(4px)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)', padding: 32, width: 420, maxWidth: '90vw',
          }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, marginBottom: 20 }}>重命名项目</h3>
            <input
              type="text" value={renameName} onChange={e => setRenameName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              placeholder="输入新名称..." autoFocus
              style={{
                width: '100%', background: 'var(--bg-input)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)', padding: '12px 16px',
                fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--text-primary)',
                outline: 'none', marginBottom: 20,
              }}
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setRenameTarget(null)} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>取消</button>
              <button onClick={handleRename} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--amber)', color: '#1a1008', cursor: 'pointer',
                boxShadow: '0 2px 8px rgba(232,153,58,0.2)',
              }}>确认</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm modal */}
      {deleteTarget && (
        <div onClick={() => setDeleteTarget(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center',
          backdropFilter: 'blur(4px)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)', padding: 32, width: 420, maxWidth: '90vw',
          }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, marginBottom: 16 }}>确认删除项目</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6, marginBottom: 8 }}>
              将删除项目 <strong style={{ color: 'var(--amber)' }}>{deleteTarget.name}</strong> 及其所有相关文件：
            </p>
            <ul style={{ color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.8, marginBottom: 20, paddingLeft: 20 }}>
              <li>上传的原始音视频文件</li>
              <li>ASR 提取的参考音频</li>
              <li>所有已生成的 TTS 配音文件</li>
              <li>切割片段和导出文件</li>
            </ul>
            <p style={{ color: 'var(--red)', fontSize: 13, marginBottom: 20 }}>此操作不可撤销。</p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setDeleteTarget(null)} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text-primary)', cursor: 'pointer',
              }}>取消</button>
              <button onClick={handleDelete} style={{
                fontFamily: 'var(--font-ui)', fontSize: 14, padding: '10px 20px',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--red)', color: '#fff', cursor: 'pointer',
              }}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
