// Google Ads Scripts collector for the CLOOP child account (258-001-5098).
var PROJECT = 'rf-ads-db-500505';
var DATASET = 'google_ads_raw';
var MALL = 'cloop';
var TABLE_BASE = 'rf_google_campaign_daily_cloop';
var TIME_ZONE = 'Asia/Seoul';
var TAB = String.fromCharCode(9);
var NL = String.fromCharCode(10);
var SCHEMA = {fields: [
  {name: 'report_date', type: 'DATE'},
  {name: 'customer_id', type: 'INTEGER'},
  {name: 'mall', type: 'STRING'},
  {name: 'campaign_id', type: 'INTEGER'},
  {name: 'campaign_name', type: 'STRING'},
  {name: 'impressions', type: 'INTEGER'},
  {name: 'clicks', type: 'INTEGER'},
  {name: 'cost', type: 'FLOAT'},
  {name: 'conversions', type: 'FLOAT'},
  {name: 'conversion_value', type: 'FLOAT'},
  {name: 'batch_time', type: 'TIMESTAMP'}
]};

function main() {
  var now = new Date();
  var batch = now.toISOString();
  var customerId = AdsApp.currentAccount().getCustomerId().split('-').join('');
  collectAndLoad('YESTERDAY', -1, now, batch, customerId);
  collectAndLoad('TODAY', 0, now, batch, customerId);
}

function collectAndLoad(period, dayOffset, now, batch, customerId) {
  var day = new Date(now.getTime() + dayOffset * 24 * 60 * 60 * 1000);
  var reportDate = Utilities.formatDate(day, TIME_ZONE, 'yyyy-MM-dd');
  var partition = Utilities.formatDate(day, TIME_ZONE, 'yyyyMMdd');
  var query = [
    'SELECT campaign.id, campaign.name, metrics.impressions, metrics.clicks,',
    'metrics.cost_micros, metrics.conversions, metrics.conversions_value',
    'FROM campaign WHERE segments.date DURING ' + period
  ].join(' ');
  var iterator = AdsApp.search(query);
  var lines = [];
  while (iterator.hasNext()) {
    var row = iterator.next();
    var campaignName = (row.campaign.name || '').split(TAB).join(' ').split(NL).join(' ');
    lines.push([
      reportDate, customerId, MALL, row.campaign.id, campaignName,
      row.metrics.impressions || 0,
      row.metrics.clicks || 0,
      (row.metrics.costMicros || 0) / 1000000,
      row.metrics.conversions || 0,
      row.metrics.conversionsValue || 0,
      batch
    ].join(TAB));
  }
  Logger.log(period + ' rows: ' + lines.length);
  if (lines.length === 0) {
    Logger.log(period + ' skipped: no rows');
    return;
  }
  BigQuery.Jobs.insert({
    configuration: {load: {
      destinationTable: {
        projectId: PROJECT,
        datasetId: DATASET,
        tableId: TABLE_BASE + '$' + partition
      },
      writeDisposition: 'WRITE_TRUNCATE',
      sourceFormat: 'CSV',
      fieldDelimiter: TAB,
      schema: SCHEMA
    }}
  }, PROJECT, Utilities.newBlob(lines.join(NL), 'application/octet-stream'));
  Logger.log(period + ' sent: ' + lines.length);
}
