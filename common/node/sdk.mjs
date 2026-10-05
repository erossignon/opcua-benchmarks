// Resolve the single locked installation independently of the caller's CWD.
export * from 'node-opcua';
export { BinaryStream } from 'node-opcua-binary-stream';
export { adjustLimitsWithParameters } from 'node-opcua-transport';
import { setDebugLogger, setWarningLogger, setErrorLogger, setTraceLogger } from 'node-opcua-debug';
for (const setLogger of [setDebugLogger, setWarningLogger, setErrorLogger, setTraceLogger]) setLogger(console.error);
