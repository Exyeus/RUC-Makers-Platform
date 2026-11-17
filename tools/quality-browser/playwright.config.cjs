const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  forbidOnly:true, testDir: '.', testMatch: 'journeys.spec.cjs', workers: 1, retries: 0, timeout: 120000,
  outputDir: process.env.QA_OUTPUT + '/test-results',
  reporter: [['list'], ['html', {outputFolder:process.env.QA_OUTPUT+'/html',open:'never'}], ['./reporter.cjs']],
  use: {baseURL:'http://127.0.0.1:8779',headless:true,channel:'chromium',trace:'on',screenshot:'on',viewport:{width:1440,height:1000}}
});


