import { expect } from 'e2e';
import { test } from './support/space-api.ts';

// Synthetic provider details only: the fake API reports a memory processor that sends its
// decisions to a decision model on OpenRouter.
const decisionProvider = {
  enabled: true,
  provider: 'openai_compatible',
  endpoint: 'https://openrouter.ai/api/v1',
  embedding_endpoint: 'https://openrouter.ai/api/v1',
  text_model: 'openai/gpt-6-luna',
  relevance_model: 'openai/gpt-6-luna',
  embedding_model: 'text-embedding-3-small',
  embedding_dim: 1536,
  relevance_provider: 'decision',
  classification_provider: 'decision',
  relationship_provider: 'disabled',
  decision_model: 'typesafe/jev-1.13',
  decision_endpoint: 'https://openrouter.ai/api/alpha/decisions',
  index: { engine: 'memora-brain-pgvector-v1', claims: 0, memories: 0, other_model: 0 },
};

test('the processing settings name the decision model and what it receives', async ({ app, screen, space }) => {
  space.memoryProvider = decisionProvider;
  await app.open('/app/settings/processing');
  await expect(screen.getByRole('heading', 'Memory processing', { level: 1 })).toBeVisible();
  await expect(screen.getByText('Decision model · typesafe/jev-1.13')).toBeVisible();
  await expect(screen.getByText('typesafe/jev-1.13 · https://openrouter.ai/api/alpha/decisions')).toBeVisible();
  await expect(screen.getByText(/Memory organization sends claims .* to the decision model typesafe\/jev-1\.13/)).toBeVisible();
  await expect(screen.getByText(/The decision model receives queries and short candidate passages/)).toBeVisible();
});

test('without a decision model the processing settings show the text assessor only', async ({ app, screen, space }) => {
  space.memoryProvider = { ...decisionProvider, relevance_provider: 'model', classification_provider: 'disabled', decision_model: null, decision_endpoint: null };
  await app.open('/app/settings/processing');
  await expect(screen.getByText('Text model').first()).toBeVisible();
  await expect(screen.getByText(/Decision model/)).toHaveCount(0);
});
