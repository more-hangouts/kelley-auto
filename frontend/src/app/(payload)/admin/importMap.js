// Custom components
import { ModelSelectComponent } from '@/collections/fields/ModelSelect'
import { VinDecoderComponent as VinDecoderComponent_0 } from '@/collections/fields/VinDecoder'

// @payloadcms/next RSC — dashboard + folder views (injected by payload/config/sanitize.js)
import {
  CollectionCards as CollectionCards_0,
  FolderField as FolderField_0,
  FolderTableCell as FolderTableCell_0,
} from '@payloadcms/next/rsc'

// @payloadcms/richtext-lexical RSC — server-side cell/field renderers
import {
  RscEntryLexicalCell as RscEntryLexicalCell_0,
  RscEntryLexicalField as RscEntryLexicalField_0,
  LexicalDiffComponent as LexicalDiffComponent_0,
} from '@payloadcms/richtext-lexical/rsc'

// @payloadcms/richtext-lexical client — all default editor feature clients
// Default features: Bold, Italic, Underline, Strikethrough, Subscript, Superscript,
// InlineCode, Paragraph, Heading, Align, Indent, UnorderedList, OrderedList, Checklist,
// Link, Relationship, Blockquote, Upload, HorizontalRule, InlineToolbar
import {
  BoldFeatureClient as BoldFeatureClient_0,
  ItalicFeatureClient as ItalicFeatureClient_0,
  UnderlineFeatureClient as UnderlineFeatureClient_0,
  StrikethroughFeatureClient as StrikethroughFeatureClient_0,
  SubscriptFeatureClient as SubscriptFeatureClient_0,
  SuperscriptFeatureClient as SuperscriptFeatureClient_0,
  InlineCodeFeatureClient as InlineCodeFeatureClient_0,
  ParagraphFeatureClient as ParagraphFeatureClient_0,
  HeadingFeatureClient as HeadingFeatureClient_0,
  AlignFeatureClient as AlignFeatureClient_0,
  IndentFeatureClient as IndentFeatureClient_0,
  UnorderedListFeatureClient as UnorderedListFeatureClient_0,
  OrderedListFeatureClient as OrderedListFeatureClient_0,
  ChecklistFeatureClient as ChecklistFeatureClient_0,
  LinkFeatureClient as LinkFeatureClient_0,
  RelationshipFeatureClient as RelationshipFeatureClient_0,
  BlockquoteFeatureClient as BlockquoteFeatureClient_0,
  UploadFeatureClient as UploadFeatureClient_0,
  HorizontalRuleFeatureClient as HorizontalRuleFeatureClient_0,
  InlineToolbarFeatureClient as InlineToolbarFeatureClient_0,
} from '@payloadcms/richtext-lexical/client'

export const importMap = {
  // Custom
  '@/collections/fields/ModelSelect#ModelSelectComponent': ModelSelectComponent,
  '@/collections/fields/VinDecoder#VinDecoderComponent': VinDecoderComponent_0,

  // Dashboard + folder views
  '@payloadcms/next/rsc#CollectionCards': CollectionCards_0,
  '@payloadcms/next/rsc#FolderField': FolderField_0,
  '@payloadcms/next/rsc#FolderTableCell': FolderTableCell_0,

  // Lexical RSC renderers
  '@payloadcms/richtext-lexical/rsc#RscEntryLexicalCell': RscEntryLexicalCell_0,
  '@payloadcms/richtext-lexical/rsc#RscEntryLexicalField': RscEntryLexicalField_0,
  '@payloadcms/richtext-lexical/rsc#LexicalDiffComponent': LexicalDiffComponent_0,

  // Lexical client features
  '@payloadcms/richtext-lexical/client#BoldFeatureClient': BoldFeatureClient_0,
  '@payloadcms/richtext-lexical/client#ItalicFeatureClient': ItalicFeatureClient_0,
  '@payloadcms/richtext-lexical/client#UnderlineFeatureClient': UnderlineFeatureClient_0,
  '@payloadcms/richtext-lexical/client#StrikethroughFeatureClient': StrikethroughFeatureClient_0,
  '@payloadcms/richtext-lexical/client#SubscriptFeatureClient': SubscriptFeatureClient_0,
  '@payloadcms/richtext-lexical/client#SuperscriptFeatureClient': SuperscriptFeatureClient_0,
  '@payloadcms/richtext-lexical/client#InlineCodeFeatureClient': InlineCodeFeatureClient_0,
  '@payloadcms/richtext-lexical/client#ParagraphFeatureClient': ParagraphFeatureClient_0,
  '@payloadcms/richtext-lexical/client#HeadingFeatureClient': HeadingFeatureClient_0,
  '@payloadcms/richtext-lexical/client#AlignFeatureClient': AlignFeatureClient_0,
  '@payloadcms/richtext-lexical/client#IndentFeatureClient': IndentFeatureClient_0,
  '@payloadcms/richtext-lexical/client#UnorderedListFeatureClient': UnorderedListFeatureClient_0,
  '@payloadcms/richtext-lexical/client#OrderedListFeatureClient': OrderedListFeatureClient_0,
  '@payloadcms/richtext-lexical/client#ChecklistFeatureClient': ChecklistFeatureClient_0,
  '@payloadcms/richtext-lexical/client#LinkFeatureClient': LinkFeatureClient_0,
  '@payloadcms/richtext-lexical/client#RelationshipFeatureClient': RelationshipFeatureClient_0,
  '@payloadcms/richtext-lexical/client#BlockquoteFeatureClient': BlockquoteFeatureClient_0,
  '@payloadcms/richtext-lexical/client#UploadFeatureClient': UploadFeatureClient_0,
  '@payloadcms/richtext-lexical/client#HorizontalRuleFeatureClient': HorizontalRuleFeatureClient_0,
  '@payloadcms/richtext-lexical/client#InlineToolbarFeatureClient': InlineToolbarFeatureClient_0,
}
