import { BrowserRouter, Routes, Route } from 'react-router-dom';
import ProjectList from './pages/ProjectList';
import ProjectEditor from './pages/ProjectEditor';
import Header from './components/Header';
import ToastProvider from './components/ToastProvider';

function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <Header />
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/project/:id" element={<ProjectEditor />} />
        </Routes>
      </ToastProvider>
    </BrowserRouter>
  );
}

export default App;
