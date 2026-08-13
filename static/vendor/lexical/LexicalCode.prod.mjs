/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *
 */

import*as e from"./LexicalCodePrism.prod.mjs";export{$createCodeHighlightNode,$createCodeNode,$getCodeLineDirection,$getEndOfCodeInLine,$getFirstCodeNodeOfLine,$getLastCodeNodeOfLine,$getStartOfCodeInLine,$isCodeHighlightNode,$isCodeNode,$outdentLeadingSpaces,CodeExtension,CodeHighlightNode,CodeIndentExtension,CodeNode,DEFAULT_CODE_LANGUAGE,getDefaultCodeLanguage}from"./LexicalCodeCore.prod.mjs";const o=e.CODE_LANGUAGE_FRIENDLY_NAME_MAP,d=e.CODE_LANGUAGE_MAP,i=e.getCodeLanguageOptions,t=e.getCodeLanguages,g=e.getCodeThemeOptions,n=e.getLanguageFriendlyName,a=e.normalizeCodeLanguage,C=e.normalizeCodeLanguage,r=e.PrismTokenizer,L=e.registerCodeHighlighting;export{o as CODE_LANGUAGE_FRIENDLY_NAME_MAP,d as CODE_LANGUAGE_MAP,r as PrismTokenizer,i as getCodeLanguageOptions,t as getCodeLanguages,g as getCodeThemeOptions,n as getLanguageFriendlyName,a as normalizeCodeLang,C as normalizeCodeLanguage,L as registerCodeHighlighting};
