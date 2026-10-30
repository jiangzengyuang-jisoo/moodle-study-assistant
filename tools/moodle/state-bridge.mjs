import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';

const codeRoot = fileURLToPath(new URL('../../', import.meta.url));
export function python(root) {
  for (const path of [process.env.STUDY_PYTHON, join(root, '.venv/Scripts/python.exe'),
    join(root, '.venv/bin/python'), join(codeRoot, '.venv/Scripts/python.exe')]) {
    if (path && existsSync(path)) return path;
  }
  return 'python';
}
export function saveState(root, mode, payload) {
  const result = spawnSync(python(root), [join(codeRoot, 'tools/study/state_io.py'),
    join(root, 'Study/_System/state.json'), mode], {
    input: JSON.stringify(payload), encoding: 'utf8', windowsHide: true,
  });
  if (result.status !== 0) throw new Error(result.stderr || '无法安全保存学习状态；请检查 Python 安装');
}
