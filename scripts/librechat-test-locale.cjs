// The upstream Lighthouse fixture uses English selectors. Scope locale to test browsers.
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', {
  paths: [path.resolve(__dirname, '../artifacts/librechat-upstream')],
}));
const launch = chromium.launch.bind(chromium);
chromium.launch = async (options) => {
  const browser = await launch(options);
  const newPage = browser.newPage.bind(browser);
  const newContext = browser.newContext.bind(browser);
  browser.newPage = (options) => newPage({ locale: 'en-US', ...options });
  browser.newContext = (options) => newContext({ locale: 'en-US', ...options });
  return browser;
};
