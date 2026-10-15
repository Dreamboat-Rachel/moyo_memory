"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.getElectronFastifyServer = getElectronFastifyServer;
exports.bootstrapApplication = bootstrapApplication;
const web_1 = require("@desktop/web");
const node_http_1 = require("node:http");
const node_path_1 = __importDefault(require("node:path"));
const certificates_routes_1 = require("./api/certificates.routes");
const credentials_routes_1 = require("./api/credentials.routes");
const desktop_routes_1 = require("./api/desktop.routes");
const dialog_routes_1 = require("./api/dialog.routes");
const electron_routes_1 = require("./api/electron.routes");
const relate_env_setup_1 = require("./api/relate-env-setup");
const user_settings_routes_1 = require("./api/user-settings.routes");
const constants_1 = require("./constants");
const handle_certificate_errors_1 = __importDefault(require("./electron/handle-certificate-errors"));
const main_logs_1 = require("./electron/main-logs");
const credentials_store_1 = require("./stores/credentials.store");
const log = (0, main_logs_1.getLogger)();
async function getElectronFastifyServer({ allowedOrigins, system, httpServer, }) {
    // Using the same underlying HTTP server for Fastify and SocketIO connections.
    const fastify = await (0, web_1.getFastifyServer)(allowedOrigins, system, httpServer);
    const { socketIo, socketIoTeardown } = await (0, web_1.getSocketIoServer)(allowedOrigins, system, httpServer);
    await fastify.register(import('@fastify/static'), { root: node_path_1.default.join(__dirname, '..', 'src', 'assets', 'public') });
    fastify.setNotFoundHandler((_req, reply) => reply.sendFile('index.html'));
    await fastify.register((0, certificates_routes_1.certificatesRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    await fastify.register((0, electron_routes_1.electronRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    await fastify.register((0, user_settings_routes_1.userSettingsRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    await fastify.register((0, credentials_routes_1.credentialsRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    await fastify.register((0, dialog_routes_1.dialogRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    await fastify.register((0, desktop_routes_1.desktopRoutes)(system), { prefix: `${web_1.FASTIFY_PROXY_PREFIX}/api` });
    return { fastify, socketIo, socketIoTeardown };
}
async function bootstrapApplication() {
    const { config, system, environment } = await (0, relate_env_setup_1.relateSetup)();
    const allowedOrigins = [
        `${config.protocol}${config.host}:${config.port}`,
        // Requests from the service worker (which proxies requests that don't go to
        // the client shared storage) have an undefined origin.
        undefined,
    ];
    allowedOrigins.push(constants_1.AURA_CONSOLE_URL);
    if (constants_1.IS_DEVELOPMENT) {
        allowedOrigins.push(`http://${constants_1.DEFAULT_DEV_URL}`);
        allowedOrigins.push(`http://localhost:3001`); // Add 3001 to allow both desktop and upx dev servers to run concurrently.
    }
    const httpServer = (0, node_http_1.createServer)();
    const { fastify, socketIoTeardown, socketIo } = await getElectronFastifyServer({
        allowedOrigins,
        system,
        httpServer,
    });
    (0, handle_certificate_errors_1.default)(socketIo);
    await fastify
        .listen({ port: config.port })
        .catch((err) => console.error('Failed to start Fastify server', err));
    log.info(`%c● ● ● ● %cRelate management API is running @ ${config.protocol}${config.host}:${config.port}`, 'color: green', 'color: cyan');
    (0, credentials_store_1.registerCredentialsManagerEventListeners)();
    return {
        environment,
        config,
        serverTeardown: socketIoTeardown,
    };
}
//# sourceMappingURL=bootstrap-application.js.map