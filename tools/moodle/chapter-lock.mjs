import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {python as choosePython} from './state-bridge.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const helper = fileURLToPath(new URL('../study/chapter_lock.py', import.meta.url));
const lost = () => Object.assign(new Error('Chapter verification lock is unavailable'), {code: 'CHAPTER_LOCK_LOST'});

export async function acquireChapterLock(path, python = choosePython(root)) {
  const child = spawn(python, ['-B', helper, path], {
    windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'],
    env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
  });
  let exited = false, released = false;
  const closed = new Promise(resolve => {
    child.once('error', () => {exited = true; resolve();});
    child.once('exit', () => {exited = true; resolve();});
  });
  // A helper killed by the OS must not turn a later release into an unhandled EPIPE.
  child.stdin.on('error', () => {exited = true;});
  const release = async () => {
    if (released) return closed;
    released = true;
    child.stdin.end();
    const timer = setTimeout(() => child.kill(), 5000);
    try {await closed;} finally {clearTimeout(timer);}
  };
  let status;
  try {
    status = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {child.kill(); reject(lost());}, 10000);
      let output = '';
      const finish = value => {clearTimeout(timer); resolve(value);};
      child.stdout.on('data', bytes => {
        output += bytes.toString('utf8');
        if (output.length > 1024) {clearTimeout(timer); reject(lost()); return;}
        if (!output.includes('\n')) return;
        try {finish(JSON.parse(output.split('\n')[0]).status);}
        catch {clearTimeout(timer); reject(lost());}
      });
      closed.then(() => {clearTimeout(timer); reject(lost());});
    });
  } catch (error) {await release(); throw error;}
  if (status !== 'acquired') {
    await release();
    if (!['busy', 'legacy_lock_needs_review'].includes(status)) throw lost();
    return {status};
  }
  return {status, helperPid: child.pid, release,
    assertHeld() {
      if (released || exited || child.exitCode !== null || child.signalCode !== null || child.stdin.destroyed) throw lost();
    }};
}
