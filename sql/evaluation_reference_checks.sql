-- QueryLens — AI-Powered Marketplace Analytics Copilot
-- Evaluation reference checks: the expected figures for the evaluation set, computed directly
-- in the warehouse (read-only, no model cost). Each query is labelled with the question ID it
-- checks in docs/05_evaluation_results.md. Each query runs on its own, as the application's
-- read-only role.

-- A1  Late share of delivered orders (expect Late 7,826 / On-Time 88,644, about 8.1%)
SELECT dimension, dimension_value, total_orders, on_time_rate, late_delivery_rate,
       avg_delivery_days, avg_review_score
FROM warehouse.vw_delivery_experience
WHERE dimension IN ('Overall', 'On-time vs Late')
ORDER BY dimension, dimension_value;

-- A2  10 slowest states
SELECT dimension_value AS state, total_orders, avg_delivery_days, on_time_rate
FROM warehouse.vw_delivery_experience
WHERE dimension = 'By State'
ORDER BY avg_delivery_days DESC, dimension_value
LIMIT 10;

-- A3 / B4  Customer status counts and churn (expect 38,487 / 33,760 / 21,111; churn 22.61%)
SELECT customer_status, COUNT(*) AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS share_pct
FROM warehouse.vw_customer_health
GROUP BY customer_status
ORDER BY customers DESC;

-- A4  Repeat-customer rate (expect about 3%)
SELECT COUNT(*) FILTER (WHERE order_count >= 2) AS repeat_customers,
       COUNT(*) AS customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE order_count >= 2) / COUNT(*), 2) AS repeat_pct
FROM warehouse.vw_rfm;

-- B1a  Best sellers by performance_score
SELECT seller_id, performance_score, performance_tier, total_orders, total_revenue
FROM warehouse.vw_seller_performance
WHERE performance_tier <> 'Insufficient Data'
ORDER BY performance_score DESC, seller_id
LIMIT 10;

-- B1b  Tier counts (expect 1,404 / 374 / 15 / 1 / 1,301)
SELECT performance_tier, COUNT(*) AS sellers
FROM warehouse.vw_seller_performance
GROUP BY performance_tier
ORDER BY sellers DESC;

-- B2  Top customers by total_spend
SELECT customer_unique_id, total_spend, order_count
FROM warehouse.vw_rfm
ORDER BY total_spend DESC, customer_unique_id
LIMIT 10;

-- B3  Total revenue, two views (should match)
SELECT 'vw_new_vs_returning_revenue' AS source, SUM(total_revenue) AS revenue, SUM(total_orders) AS orders
FROM warehouse.vw_new_vs_returning_revenue
UNION ALL
SELECT 'vw_regional_analysis', SUM(total_revenue), SUM(total_orders)
FROM warehouse.vw_regional_analysis;

-- B5  Most popular categories (by orders)
SELECT category, total_orders, total_revenue
FROM warehouse.vw_product_customer_affinity
ORDER BY total_orders DESC, category
LIMIT 10;

-- C1  RFM segment counts (expect Champions 3,784; total 93,358)
SELECT segment, COUNT(*) AS customers
FROM warehouse.vw_rfm
GROUP BY segment
ORDER BY customers DESC;

-- C2  Customer counts three ways (expect 93,358 / 96,096 / 99,441)
SELECT (SELECT COUNT(*) FROM warehouse.vw_rfm) AS customers_with_delivered_order,
       (SELECT COUNT(DISTINCT customer_unique_id) FROM warehouse.dim_customer) AS all_unique_customers,
       (SELECT COUNT(DISTINCT customer_id) FROM warehouse.dim_customer) AS customer_ids_wrong_count;

-- C3  Sellers with the most orders (all statuses vs delivered)
SELECT seller_id, total_orders, delivered_orders, total_revenue
FROM warehouse.vw_seller_performance
ORDER BY total_orders DESC, seller_id
LIMIT 10;

-- C4  Review counts (view vs distinct review_id vs raw rows 99,224)
SELECT (SELECT SUM(review_count) FROM warehouse.vw_review_response_intelligence
        WHERE dimension = 'Score Distribution') AS view_review_count,
       (SELECT COUNT(DISTINCT review_id) FROM warehouse.fact_reviews) AS distinct_review_ids,
       (SELECT COUNT(*) FROM warehouse.fact_reviews) AS raw_rows;

-- C5  Customers' survey response time by score (expect about 71-77 hours)
SELECT dimension_value AS review_score, review_count, avg_response_hours
FROM warehouse.vw_review_response_intelligence
WHERE dimension = 'Response Time'
ORDER BY dimension_value;

-- C6  Seller clusters (last check: Elite 1,525 / Non-Elite 269)
SELECT cluster_name, COUNT(*) AS sellers, MAX(computed_at) AS computed_at
FROM warehouse.ml_seller_clusters
GROUP BY cluster_name
ORDER BY cluster_name;

-- D3  Where the data ends (expect delivered revenue to Aug 2018; orders to Oct 2018)
SELECT (SELECT MAX(order_month) FROM warehouse.vw_new_vs_returning_revenue WHERE total_orders > 0) AS last_revenue_month,
       (SELECT MAX(order_purchase_timestamp)::date FROM warehouse.dim_order) AS last_order_any_status;

-- E1  Jan-Aug 2018 vs Jan-Aug 2017
SELECT EXTRACT(YEAR FROM order_month) AS year,
       SUM(total_revenue) AS revenue_jan_aug, SUM(total_orders) AS orders_jan_aug
FROM warehouse.vw_new_vs_returning_revenue
WHERE EXTRACT(MONTH FROM order_month) BETWEEN 1 AND 8
  AND EXTRACT(YEAR FROM order_month) IN (2017, 2018)
GROUP BY 1
ORDER BY 1;

-- E2  High-revenue categories (top quarter) with below-average reviews
--     Shows both readings of "average": weighted by reviews, and mean of category averages
WITH stats AS (
    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY total_revenue) AS p75_revenue,
           SUM(avg_review_score * total_reviews) / NULLIF(SUM(total_reviews), 0) AS overall_avg_review,
           AVG(avg_review_score) AS mean_of_category_avgs
    FROM warehouse.vw_product_customer_affinity
    WHERE total_reviews > 0
)
SELECT c.category, c.total_revenue, c.avg_review_score, c.total_reviews,
       ROUND(s.p75_revenue::numeric, 2) AS p75_revenue,
       ROUND(s.overall_avg_review, 3) AS overall_avg_review,
       ROUND(s.mean_of_category_avgs, 3) AS mean_of_category_avgs
FROM warehouse.vw_product_customer_affinity c
CROSS JOIN stats s
WHERE c.total_revenue >= s.p75_revenue
  AND c.avg_review_score < s.overall_avg_review
ORDER BY c.avg_review_score, c.category;

-- E3  Returning-customer revenue share by month
SELECT order_month, total_revenue, returning_revenue_pct, returning_pct_3m_rolling_avg
FROM warehouse.vw_new_vs_returning_revenue
ORDER BY order_month;

-- E4  Best 3-month retention by cohort
SELECT cohort_month, cohort_size, retained_customers, retention_rate_pct
FROM warehouse.vw_retention
WHERE period_number = 3
ORDER BY retention_rate_pct DESC, cohort_month
LIMIT 10;

-- E5  Monthly revenue for the 5 biggest categories (raw-table fallback; no view covers this)
WITH items AS (
    SELECT COALESCE(p.product_category_name_english, 'uncategorised') AS category,
           DATE_TRUNC('month', o.order_purchase_timestamp)::date AS month,
           i.price + i.freight_value AS revenue
    FROM warehouse.fact_order_items i
    JOIN warehouse.dim_order o ON o.order_key = i.order_key
    JOIN warehouse.dim_product p ON p.product_key = i.product_key
    WHERE o.order_status = 'delivered'
),
top_categories AS (
    SELECT category
    FROM items
    GROUP BY category
    ORDER BY SUM(revenue) DESC, category
    LIMIT 5
)
SELECT i.category, i.month, ROUND(SUM(i.revenue), 2) AS revenue
FROM items i
JOIN top_categories t USING (category)
GROUP BY i.category, i.month
ORDER BY i.category, i.month;

-- E6  Sellers qualifying for the orders-vs-reviews scatter (over 500 means the answer is a subset)
SELECT COUNT(*) AS qualifying_sellers,
       ROUND(CORR(total_orders, avg_review_score)::numeric, 3) AS correlation
FROM warehouse.vw_seller_performance
WHERE total_reviews >= 5;

-- F1  Top categories by revenue, with their review scores
SELECT category, total_revenue, avg_review_score, total_reviews
FROM warehouse.vw_product_customer_affinity
ORDER BY total_revenue DESC, category
LIMIT 10;

-- F2 / G5  Monthly revenue, orders and order value (2017 and 2018)
SELECT order_month, total_revenue, total_orders,
       ROUND(total_revenue / NULLIF(total_orders, 0), 2) AS revenue_per_order,
       new_revenue_pct, returning_revenue_pct
FROM warehouse.vw_new_vs_returning_revenue
WHERE order_month >= '2017-01-01'
ORDER BY order_month;
