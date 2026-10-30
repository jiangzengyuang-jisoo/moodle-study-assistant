import {spawnSync} from 'node:child_process';
import {join} from 'node:path';

// Decrypt the captured bytes, not a path that could switch accounts mid-request.
export function decryptWindowsToken(ciphertext) {
  const shell = join(process.env.SystemRoot ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const result = spawnSync(shell, ['-NoProfile', '-NonInteractive', '-Command',
    'Add-Type -AssemblyName System.Security; [Console]::Out.Write([Text.Encoding]::UTF8.GetString(' +
    '[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String([Console]::In.ReadToEnd()),' +
    '$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)))'], {
    encoding: 'utf8', windowsHide: true, input: ciphertext.toString('base64'),
  });
  if (result.status !== 0 || !/^[a-zA-Z0-9]+$/.test(result.stdout ?? ''))
    throw new Error('请先运行 connect.cmd；Token 只能由保存它的 Windows 账户使用');
  return result.stdout;
}
