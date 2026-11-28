# GAS-01-R1 Revision 2

Status **PARTIAL**. Both swap estimates are blocked. The new Mainnet sender is verified and configured. The [original GAS-01 observations](../GAS-01/verification.md) describe the superseded sender.

Baseline commit `fca1a7c657b083c3698693b947cd2e788a7bceab`, committed tree `ae60efa35c7535cb4936c6f7a50a9ff53a8e47fe`. Changes remain in the local working tree.

## Account and prerequisites

On 2026-11-27, the Mainnet Mirror Node account lookup by `0.0.10969490` and reverse lookup by `0x0000000000000000000000000000000000a76192` returned the same account, `deleted: false`, with a null alias and zero automatic token associations. The configured value comes from the response's `evm_address` field. The Mainnet ledger ID and HIP-15 algorithm yielded checksum `zqkfm`. Hashio returned chain ID `0x127`, accepted the address and reached the router when estimating both swaps. [Account record](https://mainnet-public.mirrornode.hedera.com/api/v1/accounts/0.0.10969490) · [Reverse lookup](https://mainnet-public.mirrornode.hedera.com/api/v1/accounts/0x0000000000000000000000000000000000a76192) · [HIP-15](https://hips.hedera.com/hip/hip-15)

| Prerequisite | Required | Observed and outcome | Source |
| --- | --- | --- | --- |
| Account | Resolvable, undeleted Mainnet account | Matches, PASS | Mirror Node forward and reverse lookups, Hashio simulation |
| HBAR | Both ERC-20 calls encode zero native value. A payable execution fee budget depends on valid gas units. | 0 HBAR. PASS for encoded value, no funded fee budget established | Mirror Node account balance and Hashio `eth_getBalance` |
| USDC balance | BUY quote 54.058755 USDC, conservative calldata ceiling 54.329049 USDC | 0, FAIL | `balanceOf` and router `getAmountsIn` |
| WHBAR balance | SELL exactly 1,000 WHBAR | 0, FAIL | `balanceOf` and SELL calldata |
| USDC association | Spend on BUY and receive on SELL | Missing, FAIL | Mirror Node token relationships returned an empty list |
| WHBAR association | Receive on BUY and spend on SELL | Missing, FAIL | Mirror Node token relationships returned an empty list |
| USDC router allowance | BUY quoted input, up to 54.329049 USDC for the encoded ceiling | 0, FAIL | Mirror Node router allowance list and token `allowance` |
| WHBAR router allowance | SELL exactly 1,000 WHBAR | 0, FAIL | Mirror Node router allowance list and token `allowance` |

The owner must decide whether to establish the missing relationships, balances and allowances outside this mission. Recompute the BUY requirements from a fresh quote before retrying. The current account also has no HBAR available for network fees.

## Route and price observations

Mainnet V1 RouterV3 `0.0.3045981` at `0x00000000000000000000000000000000002e7a5d` exposed both published selectors in deployed runtime bytecode. Mirror Node confirmed USDC `0.0.456858` (six decimals) and WHBAR `0.0.1456986` (eight decimals). Block-tagged router quotes matched quotes calculated from the pool reserves.

| Route | ABI and encoded bounds | Hashio result |
| --- | --- | --- |
| BUY_DEX | `0x8803dbee`, `swapTokensForExactTokens`, USDC → WHBAR. 100,000,000,000 WHBAR units out, 54,329,049 USDC units maximum input. | RPC 3, `TOKEN_NOT_ASSOCIATED_TO_ACCOUNT`; no gas units |
| SELL_DEX | `0x38ed1739`, `swapExactTokensForTokens`, WHBAR → USDC. 100,000,000,000 WHBAR units in, 53,437,690 USDC units minimum output from a 53.706222 USDC quote. | RPC 3, `TOKEN_NOT_ASSOCIATED_TO_ACCOUNT`; no gas units |

The existing encoder used the configured sender as caller and recipient, a 160-byte path offset, two token addresses, zero native value, and deadline 16:45:17 UTC. The request start was 16:45:15.438 UTC and the final head was read by 16:45:16.089 UTC. The initial block `0x6215464` and hash `0x08b27b980b57df8c14006b0ab8a16b23cb647080bcb386362762e9ff825a906c` matched Mirror Node, whose end timestamp was 16:45:07.802 UTC. The final head was `0x6215465`, a one-block advance within the bracket. Neither result produced admission evidence.

`eth_gasPrice` returned `0x17233629c00`, 1,590,000,000,000 weibars per gas, equivalent to 159 tinybars per gas and matching the Mirror Node EthereumTransaction schedule. The 2× cap is 318 tinybars per gas. For a valid gas quantity, cost in tinybars is the ceiling of gas units times weibars per gas divided by 10¹⁰. HBAR is tinybars divided by 10⁸. USDT cost uses the fresh, fee-free KuCoin 1,000 HBAR ask and rounds up in USDT micro-units under USDC/USDT parity. Observed and capped HBAR and USDT costs are unavailable for both routes.

## GAS-B02

Hashio accepted the sampled numeric block parameter for the BUY estimate and returned the same association error as for `latest`. It rejected a far-future numeric parameter with `-32602`. As a historical-state control, the same unsigned router `factory()` request from the approved sender returned `0x5ee2` from `eth_estimateGas` at both `latest` and block `0x1`. The latter header predates the router and account, and `eth_call` at that block returned sender account not found. These observations show that accepting a numeric tag does not establish historical-state estimation. They are consistent with the pinned relay source, which accepts `blockParam` and omits it from the Mirror Node estimate. The control estimate is unrelated to swap costs. [Relay implementation](https://github.com/hiero-ledger/hiero-json-rpc-relay/blob/e7a3833bea1fd4e357169e753fdc890635a64f75/src/relay/lib/services/ethService/contractService/ContractService.ts#L181-L200)

Current successful estimates still require the same head number and hash before and after, plus existing freshness and route checks. A standalone probe and prior repository evidence show the bracket can advance. This run cannot establish the success rate with an executable sender. Safe remedies are to measure verified success after prerequisites are restored, start a fresh bracket after an advance within existing request budgets, or qualify a provider with demonstrated historical-state estimation before proposing a provenance policy change to Agent 0. Offline regressions exercise both a changed block number and a changed hash at the same height.

## Diagnostic contract for the future readiness gate

Use a stable code with route, token, source, observation time, and required and observed values when available. A prerequisite can be failed or unknown independently of the adapter's compatible route-level outcome. Keep raw provider messages and request IDs out of the typed contract.

| Diagnostic code | Evidence required | This mission |
| --- | --- | --- |
| `sender_account_not_found` | Authoritative absence for the verified network and address, corroborated where possible | Account resolved |
| `insufficient_sender_hbar` | Observed tinybars below a known required HBAR budget | Zero observed. Positive fee budget unquantifiable without route gas |
| `insufficient_sender_whbar` | Spendable WHBAR below exact input | Confirmed on SELL |
| `insufficient_sender_usdc` | Spendable USDC below quoted input or configured conservative ceiling | Confirmed on BUY |
| `missing_token_association` | Authoritative relationship absence or explicit HTS status, with token when independently confirmed | Confirmed for both tokens |
| `insufficient_allowance` | Verified router allowance below the route requirement | Confirmed for both input tokens |
| `provider_timeout` | Transport deadline or explicit timeout | Seen on a preliminary lookup, resolved by retry |
| `provider_rate_limited` | HTTP 429 or explicit rate-limit code | Unobserved |
| `provider_disagreement` | Conflicting successful observations for the same network and state | Unobserved |
| `invalid_calldata` | ABI validation failure or explicit provider rejection attributable to calldata | Unobserved |
| `block_provenance_mismatch` | Mirror identity mismatch or differing head number/hash in the estimation bracket | Head advanced |
| `route_estimate_unavailable` | No usable route response, with a more specific diagnostic when established | Both swaps |

The existing adapter maps the observed router reverts to `route_estimate_unavailable` and the coordinator reports `gas_unknown`. Invalid, stale or mismatched evidence follows existing `gas_invalid` handling, including `route_estimate_head_changed` for an otherwise valid estimate invalidated by the final bracket. A timeout or rate limit leaves account existence unknown. The readiness contract is documented here without implementing the gate.

## Offline verification

On Python 3.12.14, `PYTHONPATH=src python -m pytest -q -p no:cacheprovider` produced 259 passed, zero failed and three opt-in public probes skipped. `python -m pip check` and `git diff --check` exited zero. The recorded runtime replay returns the observed HTS association error for the configured sender and verifies that neither direction admits a simulated trade.
