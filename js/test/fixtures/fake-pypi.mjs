// Preloaded with --import by follow-pydiggs.test.ts: answers PyPI's JSON API from the
// FAKE_PYPI environment variable ({url: body}) instead of the network.
const answers = JSON.parse(process.env.FAKE_PYPI ?? "{}");
globalThis.fetch = async (url) => {
  const body = answers[String(url)];
  return body === undefined ? new Response("not found", { status: 404 }) : Response.json(body);
};
