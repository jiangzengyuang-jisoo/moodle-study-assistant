import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {assertProfileAccount} from './content-policy.mjs';

// Private generation check for a long-lived MCP process. Never return either
// authorization bytes or their fingerprint to the model/logs/receipt.
function generation(config, credential) {
  return createHash('sha256').update(JSON.stringify(config)).update('\0').update(credential).digest('hex');
}

function restartRequired() {
  const error = new Error('学校连接配置或授权已变化，或当前无法核对；请重新连接 Moodle 工具后再试。');
  error.code = 'STUDY_MCP_RESTART_REQUIRED';
  return error;
}

export function createMcpChapterContext({root, loadState, client, config, credential}) {
  const workspace = resolve(root);
  const expected = generation(config, credential);
  const beforeCheck = () => {
    let current;
    try {
      const currentConfig = JSON.parse(readFileSync(join(workspace, 'config.local.json'), 'utf8').replace(/^\uFEFF/, ''));
      const currentCredential = readFileSync(join(workspace, '.private/moodle-token.dpapi'));
      current = generation(currentConfig, currentCredential);
    } catch { throw restartRequired(); }
    if (current !== expected) throw restartRequired();
    try {
      assertProfileAccount({state: loadState(), root: workspace, site: client.baseUrl,
        userId: client.userId, action: '章节核对'});
    } catch {
      const error = new Error('同一学习档案无法与当前学校连接确认一致，已停止章节核对。');
      error.code = 'PROFILE_ACCOUNT_GUARD';
      throw error;
    }
  };
  return {
    root: workspace, loadState, beforeCheck,
    connect: async () => {
      beforeCheck();
      // Revalidate identity only when the chapter engine needs the network;
      // cached checks do not invoke this or spawn another decryption process.
      await client.connect();
      beforeCheck();
      return client;
    },
  };
}
