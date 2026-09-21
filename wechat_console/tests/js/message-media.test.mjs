import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = {};
const { messageMediaViewModel } = await import("../../static/js/views/messages.js");

test("image + media_id + filename='' -> media URL exists", () => {
  const vm = messageMediaViewModel({
    type: "image",
    media_id: "img-abc12345",
    filename: "",
    mime_type: "",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.equal(vm.hasArtifact, true);
  assert.equal(vm.isImage, true);
  assert.equal(Boolean(vm.mediaId), true);
});

test("sticker + media_id + filename='' -> media URL exists", () => {
  const vm = messageMediaViewModel({
    type: "sticker",
    media_id: "stk-fedcba98",
    filename: "",
    mime_type: "image/gif",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.equal(vm.hasArtifact, true);
  assert.equal(vm.isImage, true);
  assert.equal(Boolean(vm.mediaId), true);
});

test("video + media_id + filename='' -> media URL exists", () => {
  const vm = messageMediaViewModel({
    type: "video",
    media_id: "vid-11223344",
    filename: "",
    mime_type: "video/mp4",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.equal(vm.hasArtifact, true);
  assert.equal(vm.isVideo, true);
  assert.equal(Boolean(vm.mediaId), true);
});

test("file + media_id + filename='' -> label is not media_id", () => {
  const vm = messageMediaViewModel({
    type: "file",
    media_id: "file-99887766",
    filename: "",
    mime_type: "application/pdf",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.notEqual(vm.fileLabel, "file-99887766");
  assert.notEqual(vm.filename, "file-99887766");
  assert.equal(vm.fileLabel, "文件");
});

test("file + media_id + filename=media_id -> label is not media_id", () => {
  const vm = messageMediaViewModel({
    type: "file",
    media_id: "file-99887766",
    filename: "file-99887766",
    mime_type: "application/pdf",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.notEqual(vm.fileLabel, "file-99887766");
  assert.notEqual(vm.filename, "file-99887766");
  assert.equal(vm.fileLabel, "文件");
});

test("file + media_id + real filename -> label shows real filename", () => {
  const vm = messageMediaViewModel({
    type: "file",
    media_id: "file-99887766",
    filename: "report.pdf",
    mime_type: "application/pdf",
  });
  assert.equal(vm.hasMediaReference, true);
  assert.equal(vm.hasResolvedFilename, true);
  assert.equal(vm.fileLabel, "report.pdf");
  assert.equal(vm.filename, "report.pdf");
});
