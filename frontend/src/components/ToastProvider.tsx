import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface ToastCtx {
  show: (msg: string) => void;
}

const ToastContext = createContext<ToastCtx>({ show: () => {} });
export const useToast = () => useContext(ToastContext);

export default function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState('');
  const [visible, setVisible] = useState(false);

  const show = useCallback((m: string) => {
    setMsg(m);
    setVisible(true);
    setTimeout(() => setVisible(false), 2200);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div style={{
        position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)',
        background: 'var(--bg-card)', border: '1px solid var(--border-light)',
        padding: '12px 24px', borderRadius: 30, fontSize: 13,
        color: 'var(--text-primary)', boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
        opacity: visible ? 1 : 0, transition: 'opacity 0.3s', zIndex: 100,
        pointerEvents: 'none',
      }}>
        {msg}
      </div>
    </ToastContext.Provider>
  );
}
