import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

// Projects
export const createProject = (name: string) => api.post('/projects', { name });
export const listProjects = () => api.get('/projects');
export const getProject = (id: string) => api.get(`/projects/${id}`);
export const renameProject = (id: string, name: string) => api.put(`/projects/${id}`, { name });
export const deleteProject = (id: string) => api.delete(`/projects/${id}`);

// Upload
export const uploadFile = (projectId: string, file: File, onProgress?: (pct: number) => void) => {
  const form = new FormData();
  form.append('file', file);
  return api.post(`/projects/${projectId}/upload`, form, {
    onUploadProgress: (e) => {
      if (e.total && onProgress) {
        onProgress(Math.round((e.loaded * 100) / e.total));
      }
    },
  });
};
export const getProjectStatus = (projectId: string) =>
  api.get(`/projects/${projectId}/status`);

// Sentences
export const updateSentence = (projectId: string, sentenceId: string, data: any) =>
  api.put(`/projects/${projectId}/sentences/${sentenceId}`, data);
export const softDeleteSentence = (projectId: string, sentenceId: string) =>
  api.delete(`/projects/${projectId}/sentences/${sentenceId}`);
export const undoLastAction = (projectId: string) =>
  api.post(`/projects/${projectId}/sentences/undo`);
export const splitSentence = (projectId: string, sentenceId: string, splitTime: number) =>
  api.post(`/projects/${projectId}/sentences/${sentenceId}/split`, { split_time: splitTime });
export const generateSentence = (projectId: string, sentenceId: string) =>
  api.post(`/projects/${projectId}/sentences/${sentenceId}/generate`);
export const generateAll = (projectId: string, sentenceIds: string[]) =>
  api.post(`/projects/${projectId}/sentences/generate-all`, { sentence_ids: sentenceIds });
export const getSentenceAudioUrl = (projectId: string, sentenceId: string) =>
  `/api/projects/${projectId}/sentences/${sentenceId}/audio`;
export const getOriginalAudioUrl = (projectId: string, sentenceId: string) =>
  `/api/projects/${projectId}/sentences/${sentenceId}/original`;

// Waveform
export const getWaveformData = (projectId: string) =>
  api.get(`/projects/${projectId}/waveform`);

// Region add (框选识别)
export const addSentenceFromRegion = (projectId: string, startTime: number, endTime: number) =>
  api.post(`/projects/${projectId}/sentences/add-from-region`, { start_time: startTime, end_time: endTime });


// Export
export const exportSentence = (projectId: string, sentenceId: string) =>
  `/api/projects/${projectId}/export/${sentenceId}`;
export const exportAll = (projectId: string) =>
  `/api/projects/${projectId}/export/all`;

// Settings
export const getSettings = () => api.get('/settings');
export const updateSettings = (data: {
  asr_model?: string;
  runninghub_api_key?: string;
  runninghub_workflow_id?: string;
}) => api.put('/settings', data);
export const getModelStatus = () => api.get('/settings/models');

// Model download
export const downloadModel = (engine: string, modelSize?: string) =>
  api.post('/settings/download-model', { engine, model_size: modelSize });
export const getDownloadStatus = () => api.get('/settings/download-model/status');
