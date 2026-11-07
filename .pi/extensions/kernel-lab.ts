// Optional, read-only Pi coding agent integration. Not required for M1-M3.
// See https://pi.dev/docs/latest/extensions
// Enable only once the manual controller has been validated.
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: 'kernel_lab_targets',
    label: 'Kernel Lab: targets',
    description: 'List configured Raspberry Pi targets. Read-only.',
    parameters: Type.Object({}),
    async execute() {
      const r = await pi.exec('kernel-lab', ['targets'], { timeout: 10_000 });
      return { content: [{ type: 'text', text: r.stdout || r.stderr }],
               details: { exitCode: r.code } };
    },
  });
  pi.registerTool({
    name: 'kernel_lab_compare',
    label: 'Kernel Lab: compare',
    description: 'Compare two previously recorded kernel experiment ids. Read-only.',
    parameters: Type.Object({
      first: Type.String({ pattern: '^[a-z][a-z0-9-]{1,63}$' }),
      second: Type.String({ pattern: '^[a-z][a-z0-9-]{1,63}$' }),
    }),
    async execute(_id, args) {
      const r = await pi.exec('kernel-lab', ['compare', args.first, args.second], { timeout: 10_000 });
      return { content: [{ type: 'text', text: r.stdout || r.stderr }],
               details: { exitCode: r.code } };
    },
  });
}
