// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

package markdown_test

import (
	"bytes"
	"testing"

	"forgejo.org/modules/markup/markdown"
	"forgejo.org/modules/setting"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/yuin/goldmark"
)

func newTestMd(t *testing.T) goldmark.Markdown {
	t.Helper()
	setting.IfcURL.PreviewServiceURL = "http://localhost:8000"
	t.Cleanup(func() { setting.IfcURL.PreviewServiceURL = "" })
	return goldmark.New(goldmark.WithExtensions(markdown.NewIfcURLExtension()))
}

func convert(t *testing.T, md goldmark.Markdown, src string) string {
	t.Helper()
	var buf bytes.Buffer
	require.NoError(t, md.Convert([]byte(src), &buf))
	return buf.String()
}

func TestIfcURL_LinkForm(t *testing.T) {
	md := newTestMd(t)
	out := convert(t, md, "[label](ifc://example.com/org/repo@heads/main?path=model.ifc)")
	assert.Contains(t, out, `<figure class="ifcurl-preview">`)
	assert.Contains(t, out, `ifc://example.com/org/repo@heads/main?path=model.ifc`)
	assert.Contains(t, out, `<img src="http://localhost:8000/preview?url=`)
	assert.Contains(t, out, `/assets/viewer.html?url=`)
}

func TestIfcURL_BareURL(t *testing.T) {
	md := newTestMd(t)
	out := convert(t, md, "ifc://example.com/org/repo@heads/main?path=model.ifc")
	assert.Contains(t, out, `<figure class="ifcurl-preview">`)
	assert.Contains(t, out, `ifc://example.com/org/repo@heads/main?path=model.ifc`)
}

func TestIfcURL_BareURLWithUnderscoreInPath(t *testing.T) {
	// goldmark splits text at _ (emphasis delimiter); transformer must reassemble siblings
	md := newTestMd(t)
	out := convert(t, md, "ifc://example.com/org/repo@HEAD?path=_test_model.ifc")
	assert.Contains(t, out, `<figure class="ifcurl-preview">`)
	assert.Contains(t, out, `_test_model.ifc`)
}

func TestIfcURL_DisabledWhenNoServiceURL(t *testing.T) {
	setting.IfcURL.PreviewServiceURL = ""
	md := goldmark.New(goldmark.WithExtensions(markdown.NewIfcURLExtension()))
	out := convert(t, md, "[label](ifc://example.com/org/repo@HEAD?path=model.ifc)")
	assert.NotContains(t, out, `<figure class="ifcurl-preview">`)
}

func TestIfcURL_HTMLEscapesURLInOutput(t *testing.T) {
	md := newTestMd(t)
	// angle brackets and quotes in the URL must be escaped in HTML attributes
	out := convert(t, md, `[label](ifc://example.com/org/repo@HEAD?path=m.ifc&x=<"'>)`)
	assert.NotContains(t, out, `<"'>`)
	assert.Contains(t, out, `&lt;`)
	assert.Contains(t, out, `&#34;`)
}

func TestIfcURL_NonIfcLinkUnchanged(t *testing.T) {
	md := newTestMd(t)
	out := convert(t, md, "[label](https://example.com/foo)")
	assert.NotContains(t, out, `<figure`)
	assert.Contains(t, out, `<a href="https://example.com/foo"`)
}

func TestIfcURL_InlineAmongText(t *testing.T) {
	md := newTestMd(t)
	out := convert(t, md, "See ifc://example.com/org/repo@HEAD?path=m.ifc for details.")
	assert.Contains(t, out, `<figure class="ifcurl-preview">`)
	assert.Contains(t, out, "for details.")
}
