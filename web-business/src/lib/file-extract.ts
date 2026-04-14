/**
 * Shared file content extraction for Cairn uploads.
 * Handles PDF, DOCX, images (via Claude Vision), and plain text.
 */

import { CAIRN_API_URL, CAIRN_API_KEY } from './api'

const MAX_TEXT_LENGTH = 50000

/**
 * Extract text content from a file buffer based on its extension.
 */
export async function extractText(
  buffer: Buffer,
  filename: string,
  ext: string,
): Promise<{ text: string; method: string }> {
  // Plain text / markdown / CSV
  if (['.txt', '.md', '.csv', '.tsv'].includes(ext)) {
    return { text: buffer.toString('utf-8').slice(0, MAX_TEXT_LENGTH), method: 'direct' }
  }

  // PDF
  if (ext === '.pdf') {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const pdfParse = require('pdf-parse') as (buf: Buffer) => Promise<{ text: string }>
      const result = await pdfParse(buffer)
      return { text: result.text.slice(0, MAX_TEXT_LENGTH), method: 'pdf-parse' }
    } catch (err) {
      return { text: `[PDF extraction failed for ${filename}: ${(err as Error).message}]`, method: 'error' }
    }
  }

  // Word (.docx)
  if (ext === '.docx' || ext === '.doc') {
    try {
      const mammoth = await import('mammoth')
      const result = await mammoth.extractRawText({ buffer })
      return { text: result.value.slice(0, MAX_TEXT_LENGTH), method: 'mammoth' }
    } catch (err) {
      return { text: `[DOCX extraction failed for ${filename}: ${(err as Error).message}]`, method: 'error' }
    }
  }

  // Images — route through Cairn API for Claude Vision
  if (['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(ext)) {
    try {
      const text = await analyzeImageViaCairn(buffer, filename, ext)
      return { text, method: 'claude-vision' }
    } catch (err) {
      return { text: `[Image analysis failed for ${filename}: ${(err as Error).message}]`, method: 'error' }
    }
  }

  return { text: `[Unsupported file type: ${ext}]`, method: 'unsupported' }
}

/**
 * Send image to Cairn backend for Claude Vision analysis.
 * Uses a dedicated endpoint on the Cairn API.
 */
async function analyzeImageViaCairn(
  buffer: Buffer,
  filename: string,
  ext: string,
): Promise<string> {
  const mimeMap: Record<string, string> = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
  }
  const mediaType = mimeMap[ext] || 'image/png'
  const base64 = buffer.toString('base64')

  const res = await fetch(`${CAIRN_API_URL}/analyze-image`, {
    method: 'POST',
    headers: {
      'X-API-Key': CAIRN_API_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      image_base64: base64,
      media_type: mediaType,
      filename,
      prompt: 'Describe this image in detail. If it contains text, transcribe all visible text. If it is a document, receipt, or sign, describe its contents. Be factual and concise.',
    }),
    signal: AbortSignal.timeout(30000),
  })

  if (!res.ok) {
    throw new Error(`Vision API returned ${res.status}`)
  }

  const data = await res.json()
  return data.description || data.text || ''
}
