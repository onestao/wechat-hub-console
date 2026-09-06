/* Agent C — Account/Profile view-model unit tests (C10, node --test).
 *
 * Covers the Identity v2 presentation contract at the view-model level:
 * profile fallback chain, avatar URL safety, mismatch blocking state,
 * advanced technical IDs, switcher labels, and graceful degradation.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  accountViewModel,
  avatarSrcOf,
  accountSwitchLabel,
} from "../../static/js/account-view-model.js";

const INSTANCE_UUID = "11111111-2222-4333-8444-555555555555";
const IDENTITY_UUID = "c56a4180-65aa-42ec-a945-5fd21dec0538";

function boundRuntimeAccount(overrides = {}) {
  return {
    account_id: "alpha",
    display_name: "工作微信",
    instance_uuid: INSTANCE_UUID,
    runtime_alias: "alpha",
    resource_key: "alpha-7a1b8c2d",
    wechat_identity_uuid: IDENTITY_UUID,
    identity_binding_state: "bound",
    logged_in_user: "wxid_rpfflqttdz4a22_7fcd",
    wechat_profile: {
      wechat_user_id: "wxid_rpfflqttdz4a22_7fcd",
      nickname: "科研助手小张",
      avatar_url: "https://wx.qlogo.example/132.png",
    },
    running: true,
    runtime_provider: "agent_wechat",
    ...overrides,
  };
}

function boundCoreAccount(overrides = {}) {
  return {
    account_id: "alpha",
    display_name: "工作微信",
    state: "online",
    sync: { healthy: true, last_event_at: new Date(Date.now() - 60_000).toISOString() },
    wechat_identity_uuid: IDENTITY_UUID,
    identity_binding_state: "bound",
    wechat_profile: {
      wechat_user_id: "wxid_rpfflqttdz4a22_7fcd",
      nickname: "科研助手小张",
      avatar_url: "https://wx.qlogo.example/132.png",
    },
    ...overrides,
  };
}

test("C10-1 real profile renders nickname first with hub name secondary", () => {
  const vm = accountViewModel(boundRuntimeAccount(), boundCoreAccount(), {});
  assert.equal(vm.displayName, "科研助手小张");
  assert.equal(vm.showHubName, true);
  assert.equal(vm.hubName, "工作微信");
  assert.equal(vm.identityMismatch, false);
  assert.equal(vm.primaryAction.id, "open");
  assert.equal(vm.tone, "good");
});

test("C10-2 no-profile fallback uses display_name and never invents identity", () => {
  const vm = accountViewModel(
    {
      account_id: "beta",
      display_name: "Beta WeChat",
      running: false,
      identity_binding_state: "unbound",
      wechat_profile: { wechat_user_id: "", nickname: "", avatar_url: "" },
    },
    null,
    {}
  );
  assert.equal(vm.displayName, "Beta WeChat");
  assert.equal(vm.showHubName, false);
  assert.equal(vm.avatarSrc, "");
  assert.equal(vm.statusText, "已停止");
});

test("C10-3 nickname missing falls back to logged_in_user wxid", () => {
  const vm = accountViewModel(
    boundRuntimeAccount({
      wechat_profile: {
        wechat_user_id: "wxid_rpfflqttdz4a22_7fcd",
        nickname: "",
        avatar_url: "",
      },
    }),
    boundCoreAccount(),
    {}
  );
  assert.equal(vm.displayName, "wxid_rpfflqttdz4a22_7fcd");
  assert.equal(vm.showHubName, true);
});

test("C10-4 avatar sources: relative allowed, absolute proxied, hostile rejected", () => {
  // http(s) source with identity → Console proxy URL (browser never trusts it)
  assert.equal(
    avatarSrcOf({ avatar_url: "https://a.b/c.png" }, IDENTITY_UUID),
    `/api/avatar/${IDENTITY_UUID}`
  );
  // relative same-origin path passes through
  assert.equal(avatarSrcOf({ avatar_url: "/api/avatar/x" }, IDENTITY_UUID), "/api/avatar/x");
  // protocol-relative and unknown schemes are rejected
  assert.equal(avatarSrcOf({ avatar_url: "//evil.example/a.png" }, IDENTITY_UUID), "");
  assert.equal(avatarSrcOf({ avatar_url: "javascript:alert(1)" }, IDENTITY_UUID), "");
  // absolute URL without identity uuid cannot be proxied → rejected
  assert.equal(avatarSrcOf({ avatar_url: "https://a.b/c.png" }, ""), "");
  // empty profile → no avatar
  assert.equal(avatarSrcOf({}, IDENTITY_UUID), "");
});

test("C10-5 identity mismatch blocks the normal primary action", () => {
  const vm = accountViewModel(
    boundRuntimeAccount({
      identity_binding_state: "mismatch",
      observed_wechat_user_id: "wxid_new_22222",
    }),
    boundCoreAccount({ identity_binding_state: "mismatch" }),
    {}
  );
  assert.equal(vm.identityMismatch, true);
  assert.equal(vm.tone, "bad");
  assert.equal(vm.primaryAction.id, "identity");
  assert.notEqual(vm.primaryAction.id, "open");
  assert.match(vm.statusText, /已暂停/);
  assert.equal(vm.mismatchInfo.boundNickname, "科研助手小张");
  assert.equal(vm.mismatchInfo.observedWxid, "wxid_new_22222");
});

test("C10-6 advanced drawer keeps canonical IDs distinct and marked technical", () => {
  const vm = accountViewModel(boundRuntimeAccount(), boundCoreAccount(), {});
  const a = vm.advanced;
  assert.equal(a.instanceUuid, INSTANCE_UUID);
  assert.equal(a.wechatIdentityUuid, IDENTITY_UUID);
  assert.equal(a.resourceKey, "alpha-7a1b8c2d");
  assert.equal(a.runtimeAlias, "alpha");
  assert.equal(a.wechatUserId, "wxid_rpfflqttdz4a22_7fcd");
  // C3 invariant: instance/identity keys are different namespaces
  assert.notEqual(a.instanceUuid, a.wechatIdentityUuid);
  assert.notEqual(a.runtimeAlias, a.instanceUuid);
});

test("C10-7 switcher label prefers nickname — display_name", () => {
  assert.equal(
    accountSwitchLabel(boundRuntimeAccount()),
    "科研助手小张 — 工作微信"
  );
  // no nickname → display_name only
  assert.equal(
    accountSwitchLabel({
      account_id: "beta",
      display_name: "Beta WeChat",
      wechat_profile: { nickname: "" },
    }),
    "Beta WeChat"
  );
  // identical nickname and display_name must not duplicate
  assert.equal(
    accountSwitchLabel({
      account_id: "gamma",
      display_name: "小王",
      wechat_profile: { nickname: "小王" },
    }),
    "小王"
  );
  // never a bare account_id unless there is nothing else
  assert.equal(accountSwitchLabel({ account_id: "delta" }), "delta");
});

test("C10-8 offline/stopped account degrades gracefully with partial data", () => {
  const vm = accountViewModel(
    {
      account_id: "stopped-1",
      running: false,
      wechat_profile: undefined,
      identity_binding_state: "",
    },
    null,
    {}
  );
  assert.equal(vm.displayName, "stopped-1");
  assert.equal(vm.statusText, "已停止");
  assert.equal(vm.avatarSrc, "");
  assert.equal(vm.advanced.instanceUuid, "--");
  assert.equal(vm.advanced.resourceKey, "--");
});

test("C10-9 rename payload mapping keeps alias untouched (view-model side)", () => {
  // The rename dialog submits display_name only; the advanced runtime_alias
  // stays read-only data that the UI never sends back.
  const vm = accountViewModel(boundRuntimeAccount(), boundCoreAccount(), {});
  assert.equal(vm.advanced.runtimeAlias, "alpha");
  assert.equal(vm.hubName, "工作微信");
  assert.equal(typeof vm.advanced.instanceUuid, "string");
});
